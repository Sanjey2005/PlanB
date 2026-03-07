import json
import re
from datetime import datetime

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_LARGE, GROQ_API_KEY
from state import PlanBState
from utils.google_calendar import get_todays_events

load_dotenv()

REPLAN_PROMPT = """\
You are a scheduling assistant replanning a day after a disruption.

Disruption summary: {context_summary}
Severity: {severity}
Hours lost today: {hours_impacted}

Today's schedule:
LOCKED (cannot move):
{immovable_list}

MOVEABLE (can reschedule):
{moveable_list}

BLOCKED (cannot happen today due to disruption):
{blocked_list}

For each moveable and blocked task, decide:
- keep: task stays at its current time
- move: task should be rescheduled (suggest a time range like 'late afternoon' or 'tomorrow morning')
- drop: task should be cancelled today

Return ONLY valid JSON as a list:
[
  {{
    "task_id": string (event id),
    "task_name": string (event summary),
    "action": one of [keep, move, drop],
    "reason": string (one sentence why),
    "old_time": string (current start time),
    "suggested_time": string (for move: suggested new time as ISO string or description, for keep/drop: same as old_time)
  }}
]

Rules:
- Never move LOCKED tasks
- Always move BLOCKED tasks
- Prioritize keeping high-score moveable tasks
- If hours_impacted >= 3, be aggressive about moving low-score tasks to tomorrow
- If severity is low, prefer keeping most things and only moving truly blocked tasks

STRICT SCHEDULING RULES — these override all other instructions. Never violate them:
- Breakfast tasks (any task with "breakfast" in the name): 6 AM – 10 AM only
- Lunch tasks (any task with "lunch" in the name): 11 AM – 2 PM only
- Dinner tasks (any task with "dinner" or "supper" in the name): 6 PM – 9 PM only
- Gym / workout / exercise / yoga / run: never before 6 AM, never after 10 PM
- Sleep / nap / rest: never move to daytime hours (9 AM – 9 PM)
- Routine or personal tasks must stay in the same time-of-day window: morning tasks stay in morning, evening tasks stay in evening
- Never suggest a move of more than 3 hours from the original time unless the task is moving to tomorrow
- If a task cannot be rescheduled to a logical time within these rules, use action "keep" and explain why\
"""


def _parse_llm_json(content: str):
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _format_event_line(event: dict, score: int | None, tag: str) -> str:
    """Format a single event as a readable line for the prompt."""
    summary = event.get("summary", "(No title)")
    start = event.get("start", "?")
    if score is not None:
        return f"{summary} at {start} [{tag} - score {score}]"
    return f"{summary} at {start} [{tag}]"


def _build_fallback(events: list, blocked_summaries: set) -> list:
    """Build safe fallback proposed_schedule when Groq/parsing fails."""
    result = []
    for event in events:
        eid = event.get("id", "")
        summary = event.get("summary", "")
        start = event.get("start", "")
        is_blocked = summary in blocked_summaries

        result.append({
            "task_id": eid,
            "task_name": summary,
            "action": "move" if is_blocked else "keep",
            "reason": "Blocked by disruption" if is_blocked else "Keeping as fallback",
            "old_time": start,
            "suggested_time": "tomorrow" if is_blocked else start,
        })
    return result


_MEAL_WINDOWS = {
    "breakfast": (6,  10),
    "lunch":     (11, 14),
    "dinner":    (18, 21),
    "supper":    (18, 21),
}
_GYM_KEYWORDS   = ("gym", "workout", "exercise", "yoga", "run", "jog", "swim", "lift", "training", "crossfit")
_SLEEP_KEYWORDS = ("sleep", "nap", "rest", "bed")
_MEAL_GENERIC   = ("food", "eat", "meal", "snack")


def _classify_task(task_name: str) -> str | None:
    """Return a category string for hard-rule checking, or None if unconstrained."""
    n = task_name.lower()
    for kw in _MEAL_WINDOWS:
        if kw in n:
            return kw
    for kw in _MEAL_GENERIC:
        if kw in n:
            return "meal_generic"
    for kw in _GYM_KEYWORDS:
        if kw in n:
            return "gym"
    for kw in _SLEEP_KEYWORDS:
        if kw in n:
            return "sleep"
    return None


def _parse_hour(suggested_time: str) -> int | None:
    """Extract the hour (0–23) from an ISO string, clock string, or descriptive phrase."""
    if not suggested_time:
        return None

    # ISO datetime
    try:
        return datetime.fromisoformat(suggested_time).hour
    except (ValueError, TypeError):
        pass

    text = suggested_time.strip()

    # "8:30 AM", "14:00", "8AM", "8 am"
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        return hour

    m = re.search(r"(\d{1,2})\s*(am|pm)", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        ampm = m.group(2).lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        return hour

    # Descriptive keywords → representative hour
    tl = text.lower()
    if "early morning" in tl:   return 6
    if "morning"       in tl:   return 8
    if "noon"          in tl or "midday" in tl: return 12
    if "early afternoon" in tl: return 13
    if "late afternoon" in tl:  return 16
    if "afternoon"     in tl:   return 14
    if "early evening" in tl:   return 17
    if "evening"       in tl:   return 18
    if "night"         in tl:   return 21

    return None


def _validate_schedule_item(item: dict) -> dict:
    """Post-LLM validation: reject illogical time moves and override to 'keep'.

    Checks the suggested_time against hard windows for meals, gym, and sleep.
    Also enforces the 3-hour drift limit for same-day moves.
    Returns a (possibly modified) copy of item.
    """
    if item.get("action") != "move":
        return item

    task_name      = item.get("task_name", "")
    suggested_time = item.get("suggested_time", "")
    old_time       = item.get("old_time", "")

    category = _classify_task(task_name)
    if category is None:
        # No hard rules for this task; still check 3-hour drift on same-day moves
        if "tomorrow" not in suggested_time.lower():
            old_hour  = _parse_hour(old_time)
            sugg_hour = _parse_hour(suggested_time)
            if old_hour is not None and sugg_hour is not None:
                if abs(sugg_hour - old_hour) > 3:
                    item = dict(item)
                    item["action"] = "keep"
                    item["suggested_time"] = old_time
                    item["reason"] = (
                        f"Suggested move for '{task_name}' is more than 3 hours from original "
                        f"({old_time} → {suggested_time}). Kept at original time to avoid disruption."
                    )
        return item

    sugg_hour = _parse_hour(suggested_time)
    if sugg_hour is None:
        # Can't parse — trust the LLM if it used a plain word like "tomorrow"
        return item

    violation = False
    reason    = ""

    if category in _MEAL_WINDOWS:
        lo, hi = _MEAL_WINDOWS[category]
        if not (lo <= sugg_hour < hi):
            violation = True
            meal_name = category.capitalize()
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"{meal_name} must be between {lo % 12 or lo}{'AM' if lo < 12 else 'PM'} "
                f"and {hi % 12 or hi}{'AM' if hi < 12 else 'PM'}. Kept at original time."
            )

    elif category == "meal_generic":
        if not (6 <= sugg_hour < 21):
            violation = True
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"meals must stay between 6 AM and 9 PM. Kept at original time."
            )

    elif category == "gym":
        if not (6 <= sugg_hour < 22):
            violation = True
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"gym/workout must be between 6 AM and 10 PM. Kept at original time."
            )

    elif category == "sleep":
        if 9 <= sugg_hour < 21:
            violation = True
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"sleep/rest cannot be moved to daytime hours. Kept at original time."
            )

    if violation:
        item = dict(item)
        item["action"]         = "keep"
        item["suggested_time"] = old_time
        item["reason"]         = reason
        print(f"Replan Agent [validation]: {reason}")

    return item


def replan_agent(state: PlanBState) -> PlanBState:
    """Replan Agent — decides WHAT moves, not WHERE.

    Categorises today's events into immovable (locked), moveable, and blocked
    based on priority scores and cascade analysis. Then asks Groq
    llama-3.3-70b-versatile to produce a keep/move/drop decision for every
    non-locked task.

    The Scheduler Agent downstream is responsible for finding actual free
    calendar slots and writing the changes.

    Reads from state:
        task_scores (dict):      {event_id: int} from Priority Engine.
        cascade_map (dict):      {directly_blocked: [...]} from Resilience Agent.
        hours_impacted (float):  From Context Agent.
        context_summary (str):   From Context Agent.
        severity (str):          From Context Agent.

    Writes to state:
        proposed_schedule (list): [{task_id, task_name, action, reason,
                                    old_time, suggested_time}]
    """
    try:
        task_scores = state.get("task_scores") or {}
        cascade_map = state.get("cascade_map") or {}
        hours_impacted = state.get("hours_impacted") or 0.0
        context_summary = state.get("context_summary") or state.get("disruption_raw") or "Unknown disruption."
        severity = state.get("severity") or "low"

        events = get_todays_events()
        if not events:
            state["proposed_schedule"] = []
            return state

        directly_blocked = set(cascade_map.get("directly_blocked", []))

        # Categorise events
        immovable = []
        moveable = []
        blocked = []

        for event in events:
            eid = event.get("id", "")
            summary = event.get("summary", "")
            score = task_scores.get(eid)
            attendees = event.get("attendees", [])

            if summary in directly_blocked:
                blocked.append((event, score))
            elif score is not None and score >= 75:
                immovable.append((event, score))
            elif score is None and attendees:
                immovable.append((event, score))
            else:
                moveable.append((event, score))

        # Format lists for the prompt
        immovable_list = "\n".join(
            _format_event_line(e, s, "LOCKED") for e, s in immovable
        ) or "(none)"
        moveable_list = "\n".join(
            _format_event_line(e, s, "score") for e, s in moveable
        ) or "(none)"
        blocked_list = "\n".join(
            _format_event_line(e, s, "BLOCKED by disruption") for e, s in blocked
        ) or "(none)"

        # Call Groq
        llm = ChatGroq(model=GROQ_MODEL_LARGE, api_key=GROQ_API_KEY)
        prompt = REPLAN_PROMPT.format(
            context_summary=context_summary,
            severity=severity,
            hours_impacted=hours_impacted,
            immovable_list=immovable_list,
            moveable_list=moveable_list,
            blocked_list=blocked_list,
        )

        response = llm.invoke(prompt)

        try:
            proposed = _parse_llm_json(response.content)
            if not isinstance(proposed, list):
                raise ValueError("Expected a JSON list")
        except (json.JSONDecodeError, IndexError, ValueError) as e:
            print(f"Replan Agent: failed to parse Groq JSON, using fallback: {e}")
            proposed = _build_fallback(events, directly_blocked)

        # Fix task_ids — Groq returns event names not real Google Calendar IDs
        summary_to_id = {e.get("summary", ""): e.get("id", "") for e in events}
        for item in proposed:
            task_name = item.get("task_name", "")
            if task_name in summary_to_id:
                item["task_id"] = summary_to_id[task_name]

        # Validate each move against hard scheduling rules before committing
        proposed = [_validate_schedule_item(item) for item in proposed]

        state["proposed_schedule"] = proposed
        return state

    except Exception as e:
        print(f"Replan Agent: unexpected error: {e}")
        state["proposed_schedule"] = []
        return state
