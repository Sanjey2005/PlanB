from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_FAST, GROQ_API_KEY
from state import PlanBState
from utils.google_calendar import get_todays_events
from utils.streak_tracker import get_drop_count_last_n_days

load_dotenv()

ROUTINE_KEYWORDS = [
    "gym", "workout", "meal", "lunch", "dinner", "breakfast", "reading",
    "prayer", "meditation", "walk", "run", "exercise", "yoga", "sleep", "nap",
]

VALID_DECISIONS = {"kept", "compressed", "delayed", "dropped"}

PROMPT_TEMPLATE = """\
You are a scheduling assistant. Decide what to do with this routine/habit block today.

Routine: {summary}
Routine priority score: {score}/100
Disruption severity: {severity}
User fatigue level: {fatigue_level}
{note}
Rules:
- If severity is high AND score < 70: drop it today
- If severity is high AND score >= 70: compress to 30 minutes
- If severity is medium AND fatigue is high: compress to 30 minutes
- If severity is medium AND score < 50: delay by 1 hour
- If severity is low: keep it
- If fatigue is high AND energy_cost is high: compress or delay

Reply with ONLY one of these exact words: kept, compressed, delayed, dropped
Then on the next line, one sentence explaining why.\
"""

STREAK_PROTECTION_BOOST = 25
STREAK_DROP_THRESHOLD = 2
STREAK_DROP_WINDOW_DAYS = 3


def _is_routine(event: dict) -> bool:
    """Return True if the event is a routine/habit block."""
    extended = event.get("extendedProperties", {})
    private = extended.get("private", {}) if isinstance(extended, dict) else {}
    if private.get("planb_task_type", "").strip().lower() == "routine":
        return True
    summary = event.get("summary", "").lower()
    return any(kw in summary for kw in ROUTINE_KEYWORDS)


def _ask_groq(llm: ChatGroq, summary: str, score: int, severity: str, fatigue_level: str,
              note: str = "") -> tuple[str, str]:
    """Call Groq and return (decision, reason). Defaults to ('kept', '') on any failure."""
    prompt = PROMPT_TEMPLATE.format(
        summary=summary,
        score=score,
        severity=severity,
        fatigue_level=fatigue_level,
        note=note,
    )
    try:
        response = llm.invoke(prompt)
        lines = response.content.strip().splitlines()
        decision = lines[0].strip().lower() if lines else "kept"
        reason = lines[1].strip() if len(lines) > 1 else ""
        if decision not in VALID_DECISIONS:
            decision = "kept"
        return decision, reason
    except Exception as e:
        print(f"Routine Agent: Groq call failed for '{summary}': {e}")
        return "kept", ""


def routine_agent(state: PlanBState) -> PlanBState:
    """Routine Agent — reasons about habit/routine blocks and decides their fate.

    Routine events are NOT locked. For each routine found in today's calendar,
    Groq weighs disruption severity, priority score, and fatigue to decide whether
    to keep, compress (to 30 min), delay (by 1 hour), or drop the block.

    Reads from state:
        task_scores (dict):     {event_id: int} priority scores from Priority Engine.
        severity (str):         Disruption severity from Context Agent.
        fatigue_level (str):    User fatigue level from Context Agent.
        proposed_schedule (list): Existing schedule from Replan Agent (optional).

    Writes to state:
        routine_decisions (dict): {event_summary: {decision, reason, event_id}}
        proposed_schedule (list): Updated in-place for dropped/compressed routines.
    """
    try:
        task_scores = state.get("task_scores") or {}
        severity = (state.get("severity") or "low").lower()
        fatigue_level = (state.get("fatigue_level") or "none").lower()

        llm = ChatGroq(model=GROQ_MODEL_FAST, api_key=GROQ_API_KEY)

        today_events = get_todays_events()
        routine_events = [e for e in today_events if _is_routine(e)]

        routine_decisions = {}

        for event in routine_events:
            event_id = event.get("id", "")
            summary = event.get("summary", "Untitled routine")
            score = task_scores.get(event_id, 50)

            # Streak protection — boost score if dropped 2+ times in last 3 days
            streak_protected = False
            drop_count = 0
            try:
                drop_count = get_drop_count_last_n_days(summary, STREAK_DROP_WINDOW_DAYS)
                if drop_count >= STREAK_DROP_THRESHOLD:
                    score = min(100, score + STREAK_PROTECTION_BOOST)
                    streak_protected = True
                    print(f"Routine Agent: streak protection active for '{summary}' "
                          f"(dropped {drop_count}x in last {STREAK_DROP_WINDOW_DAYS} days, score boosted to {score})")
            except Exception as e:
                print(f"Routine Agent: streak tracker failed for '{summary}': {e}")

            note = "Note: streak protection active — boosted score" if streak_protected else ""
            decision, reason = _ask_groq(llm, summary, score, severity, fatigue_level, note=note)

            routine_decisions[summary] = {
                "decision": decision,
                "reason": reason,
                "event_id": event_id,
                "streak_protected": streak_protected,
                "drop_count": drop_count,
            }

        state["routine_decisions"] = routine_decisions

        # STEP 4 — Patch proposed_schedule if it exists
        proposed = state.get("proposed_schedule")
        if proposed is not None:
            summary_to_decision = {
                summary: data["decision"]
                for summary, data in routine_decisions.items()
                if data["decision"] in ("dropped", "compressed")
            }
            for slot in proposed:
                slot_summary = slot.get("summary", "")
                if slot_summary in summary_to_decision:
                    slot["action"] = summary_to_decision[slot_summary]
            state["proposed_schedule"] = proposed

        return state

    except Exception as e:
        print(f"Routine Agent: unexpected error: {e}")
        state["routine_decisions"] = {}
        return state
