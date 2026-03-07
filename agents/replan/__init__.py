from datetime import datetime

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_LARGE, GROQ_API_KEY
from state import PlanBState
from utils.google_calendar import get_todays_events
from utils.llm_utils import parse_llm_json
from utils.scheduling_rules import validate_schedule_item

load_dotenv()

REPLAN_PROMPT = """\
You are a scheduling assistant replanning a day after a disruption.

Disruption summary: {context_summary}
Severity: {severity}
Hours lost today: {hours_impacted}
Current wall-clock time (IST): {current_time}
Current hour (24h): {current_hour}

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

TIME-AWARENESS RULES — highest priority, override all other rules:
- Any event whose start time is BEFORE {current_time} has already occurred. ALWAYS use action "keep" for these. Never suggest moving a past event.
- If the user's request is about "clearing", "cancelling all", or "rescheduling rest of day": only move events that start AFTER {current_time}. Events before {current_time} are already done.
- When moving an event to tomorrow, default suggested_time to "tomorrow 7:00 AM" unless a specific time fits better.

Rules:
- Never move LOCKED tasks
- Always move BLOCKED tasks
- Prioritize keeping high-score moveable tasks
- If hours_impacted >= 3, be aggressive about moving low-score tasks to tomorrow
- If severity is low, prefer keeping most things and only moving truly blocked tasks

STRICT SCHEDULING RULES — these override all other instructions except time-awareness. Never violate them:
- Breakfast tasks (any task with "breakfast" in the name): 6 AM – 10 AM only
- Lunch tasks (any task with "lunch" in the name): 11 AM – 2 PM only
- Dinner tasks (any task with "dinner" or "supper" in the name): 6 PM – 9 PM only
- Gym / workout / exercise / yoga / run: never before 6 AM, never after 10 PM
- Sleep / nap / rest: never move to daytime hours (9 AM – 9 PM)
- Routine or personal tasks must stay in the same time-of-day window: morning tasks stay in morning, evening tasks stay in evening
- Never suggest a move of more than 3 hours from the original time unless the task is moving to tomorrow
- If a task cannot be rescheduled to a logical time within these rules, use action "keep" and explain why\
"""


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
        current_time = state.get("current_time") or ""
        current_hour = state.get("current_hour")

        events = get_todays_events(phone=state.get("user_phone"))
        if not events:
            state["proposed_schedule"] = []
            return state

        directly_blocked = set(cascade_map.get("directly_blocked", []))

        # Build set of past event IDs — events that started before current_time are already done
        past_event_ids: set = set()
        if current_time:
            try:
                now_dt = datetime.fromisoformat(current_time)
                for event in events:
                    start_raw = event.get("start", "")
                    if start_raw:
                        try:
                            import re as _re
                            start_normalised = _re.sub(r"[+-]\d{2}:\d{2}$", "", start_raw)
                            event_dt = datetime.fromisoformat(start_normalised)
                            # Strip timezone from now_dt for naive comparison
                            now_naive = now_dt.replace(tzinfo=None)
                            if event_dt < now_naive:
                                past_event_ids.add(event.get("id", ""))
                        except Exception:
                            pass
            except Exception:
                pass

        # Categorise events
        immovable = []
        moveable = []
        blocked = []

        for event in events:
            eid = event.get("id", "")
            summary = event.get("summary", "")
            score = task_scores.get(eid)
            attendees = event.get("attendees", [])

            # Past events are always immovable — they already happened
            if eid in past_event_ids:
                immovable.append((event, score))
                continue

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
            current_time=current_time or "unknown",
            current_hour=current_hour if current_hour is not None else 0,
            immovable_list=immovable_list,
            moveable_list=moveable_list,
            blocked_list=blocked_list,
        )

        response = llm.invoke(prompt)

        try:
            proposed = parse_llm_json(response.content)
            if not isinstance(proposed, list):
                raise ValueError("Expected a JSON list")
        except (Exception) as e:
            print(f"Replan Agent: failed to parse Groq JSON, using fallback: {e}")
            proposed = _build_fallback(events, directly_blocked)

        # Fix task_ids — Groq returns event names not real Google Calendar IDs
        summary_to_id = {e.get("summary", ""): e.get("id", "") for e in events}
        for item in proposed:
            task_name = item.get("task_name", "")
            if task_name in summary_to_id:
                item["task_id"] = summary_to_id[task_name]

        # Validate each move against hard scheduling rules before committing
        proposed = [validate_schedule_item(item) for item in proposed]

        state["proposed_schedule"] = proposed
        return state

    except Exception as e:
        print(f"Replan Agent: unexpected error: {e}")
        state["proposed_schedule"] = []
        return state
