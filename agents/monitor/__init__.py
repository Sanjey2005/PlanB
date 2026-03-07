import os

from dotenv import load_dotenv

from config import settings
from state import PlanBState
from utils.gmail_reader import get_recent_emails, understand_email_with_gemini
from utils.google_calendar import get_todays_events, get_events_range
from utils.user_dna import is_new_user
from utils.keywords import (
    CALENDAR_CONNECT_KEYWORDS, STRESS_KEYWORDS, CRISIS_KEYWORDS,
    DISRUPTION_KEYWORDS, QUERY_KEYWORDS, HABIT_STATS_KEYWORDS,
    BUFFER_KEYWORDS, UNDO_KEYWORDS, LATE_OFFICE_KEYWORDS,
    HUNGRY_KEYWORDS, CAB_KEYWORDS, SCHEDULE_REQUEST_VERBS,
    SCHEDULABLE_ITEMS, CLEAR_SCHEDULE_KEYWORDS, APPROVAL_KEYWORDS,
    ROUTINE_SETUP_KEYWORDS,
)

load_dotenv()


def _looks_like_disruption(text: str) -> bool:
    """Heuristic: does the message imply a schedule change without matching keywords?"""
    time_signals = any(w in text for w in [
        "today", "this morning", "this afternoon", "this evening",
        "now", "just", "suddenly", "afternoon", "morning",
    ])
    disruption_signals = any(w in text for w in [
        "can't", "cant", "cannot", "won't", "wont", "not able", "unable",
        "have to", "need to", "got to", "gotta",
        "no longer", "not happening", "push", "move",
        "different", "changed", "fell", "broke",
    ])
    schedule_signals = any(w in text for w in [
        "meeting", "appointment", "plan", "schedule", "calendar",
        "today", "tomorrow", "morning", "afternoon", "evening",
        "work", "office", "gym", "lunch", "call",
    ])
    return time_signals and disruption_signals and schedule_signals


def monitor_agent(state: PlanBState) -> PlanBState:
    """Monitor Agent — entry point for all pipeline triggers.

    Handles three disruption sources:
    - gmail_webhook: Analyses raw email text with Gemini to detect disruptions.
    - user_message: Scans WhatsApp message for disruption or query keywords.
    - scheduled: Fetches today's calendar events and builds a summary for
      morning_briefing / evening_review modes.

    Writes to state: mode, disruption_raw (updated where needed).
    """
    source = state.get("disruption_source")

    # JOB 1 — Incoming Gmail webhook with raw email body
    if source == "gmail_webhook":
        raw = state.get("disruption_raw", "")
        result = understand_email_with_gemini(raw)
        if result.get("is_disruption"):
            state["disruption_raw"] = result.get("summary", raw)
            state["mode"] = "disruption"
        else:
            state["mode"] = "query"
        return state

    # JOB 2 — User sent a WhatsApp message directly
    if source == "user_message":
        # New user check — runs before all keyword detection.
        # A brand-new phone number always triggers onboarding, regardless of what they typed.
        user_phone = state.get("user_phone") or ""
        if user_phone and is_new_user(user_phone):
            state["mode"] = "onboarding"
            state["is_new_user"] = True
            return state

        message = (state.get("disruption_raw") or "").lower()
        if any(kw in message for kw in APPROVAL_KEYWORDS):
            state["mode"] = "apply_proposals"
            return state
        elif any(kw in message for kw in CALENDAR_CONNECT_KEYWORDS):
            state["mode"] = "on_demand"
            state["disruption_raw"] = "CALENDAR_CONNECT_REQUEST"
        elif any(kw in message for kw in STRESS_KEYWORDS):
            state["mode"] = "stress"
            state["stress_mode"] = True
        elif any(kw in message for kw in CRISIS_KEYWORDS):
            state["mode"] = "crisis"
            state["crisis_mode"] = True
        elif any(kw in message for kw in HABIT_STATS_KEYWORDS):
            state["mode"] = "query"
            state["disruption_raw"] = "HABIT_STATS_REQUEST"
        elif any(kw in message for kw in BUFFER_KEYWORDS):
            state["mode"] = "on_demand"
            state["disruption_raw"] = "BUFFER_REQUEST"
        elif any(kw in message for kw in UNDO_KEYWORDS):
            state["mode"] = "undo"
            state["disruption_raw"] = "UNDO_REQUEST"
        elif any(kw in message for kw in LATE_OFFICE_KEYWORDS + HUNGRY_KEYWORDS + CAB_KEYWORDS):
            state["mode"] = "lifestyle"
            # disruption_raw already holds the original message — no overwrite needed
        elif (any(kw in message for kw in ROUTINE_SETUP_KEYWORDS)
              and (any(n in message for n in SCHEDULABLE_ITEMS)
                   or any(c in message for c in ["am", "pm", ":"]))):
            state["mode"] = "routine_setup"
        elif (any(v in message for v in SCHEDULE_REQUEST_VERBS)
              and any(n in message for n in SCHEDULABLE_ITEMS)
              and not any(a in message for a in ("cancel", "postpone", "drop", "remove", "delete"))):
            state["mode"] = "on_demand"
        elif any(kw in message for kw in CLEAR_SCHEDULE_KEYWORDS):
            state["mode"] = "disruption"
        elif any(kw in message for kw in DISRUPTION_KEYWORDS):
            state["mode"] = "disruption"
        elif any(kw in message for kw in QUERY_KEYWORDS):
            state["mode"] = "query"
        elif _looks_like_disruption(message):
            state["mode"] = "disruption"
        else:
            state["mode"] = "on_demand"
        return state

    # JOB 3 — Scheduled trigger (morning_briefing or evening_review)
    if source == "scheduled":
        events = get_todays_events(phone=state.get("user_phone"))
        if events:
            lines = ["Today's schedule:"]
            for event in events:
                start = event.get("start", "")
                summary = event.get("summary", "(No title)")
                lines.append(f"  - {start}: {summary}")
            state["disruption_raw"] = "\n".join(lines)
        else:
            state["disruption_raw"] = "No events scheduled for today."

        # Preserve mode if already set to morning_briefing, evening_review, or weekly_scan
        current_mode = state.get("mode")
        if current_mode not in ("morning_briefing", "evening_review", "weekly_scan"):
            state["mode"] = "morning_briefing"

        return state

    # Default — unknown source, pass through unchanged
    return state
