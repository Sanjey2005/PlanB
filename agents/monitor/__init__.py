import os

from dotenv import load_dotenv

from config import settings
from state import PlanBState
from utils.gmail_reader import get_recent_emails, understand_email_with_gemini
from utils.google_calendar import get_todays_events, get_events_range

load_dotenv()

STRESS_KEYWORDS = [
    "overwhelmed", "i'm overwhelmed", "stressed", "i'm stressed",
    "burned out", "burnt out", "anxious", "too much", "can't cope", "exhausted mentally",
]
CRISIS_KEYWORDS = [
    "crisis mode", "panic", "emergency", "i'm sick", "deadline emergency",
]
DISRUPTION_KEYWORDS = [
    "delayed", "cancelled", "sick", "headache", "tired", "meeting",
    "rescheduled", "emergency", "traffic", "flight", "late", "cancel",
    "postpone", "unwell", "exhausted", "overran", "ran over",
]
QUERY_KEYWORDS = ["what", "show", "list", "when", "schedule"]
HABIT_STATS_KEYWORDS = ["my stats", "show my habits"]
BUFFER_KEYWORDS = ["buffer it", "add buffers"]
UNDO_KEYWORDS = ["undo", "revert", "undo that", "put it back", "reverse that"]
LATE_OFFICE_KEYWORDS = [
    "staying late", "working late", "stuck in office", "late at office",
    "cant leave", "still at work", "working overtime",
]
HUNGRY_KEYWORDS = [
    "hungry", "starving", "need food", "order food", "what should i eat", "food",
]
CAB_KEYWORDS = [
    "book cab", "need a ride", "going home", "leaving office",
    "how do i get home", "book uber", "book ola",
]


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
        message = (state.get("disruption_raw") or "").lower()
        if any(kw in message for kw in STRESS_KEYWORDS):
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
        elif any(kw in message for kw in DISRUPTION_KEYWORDS):
            state["mode"] = "disruption"
        elif any(kw in message for kw in QUERY_KEYWORDS):
            state["mode"] = "query"
        else:
            state["mode"] = "on_demand"
        return state

    # JOB 3 — Scheduled trigger (morning_briefing or evening_review)
    if source == "scheduled":
        events = get_todays_events()
        if events:
            lines = ["Today's schedule:"]
            for event in events:
                start = event.get("start", "")
                summary = event.get("summary", "(No title)")
                lines.append(f"  - {start}: {summary}")
            state["disruption_raw"] = "\n".join(lines)
        else:
            state["disruption_raw"] = "No events scheduled for today."

        # Preserve mode if already set to morning_briefing or evening_review
        current_mode = state.get("mode")
        if current_mode not in ("morning_briefing", "evening_review"):
            state["mode"] = "morning_briefing"

        return state

    # Default — unknown source, pass through unchanged
    return state
