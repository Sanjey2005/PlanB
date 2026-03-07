"""
Shared keyword lists and timezone constants for PlanB agents.

All keyword constants that were previously defined in agents/monitor/__init__.py
are centralised here so other agents can import them without creating circular
dependencies.
"""

from datetime import timezone, timedelta

# ── Timezone ────────────────────────────────────────────────────────────────────
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))
IST_OFFSET_STR = "+05:30"

# ── Keyword lists ───────────────────────────────────────────────────────────────

CALENDAR_CONNECT_KEYWORDS = ["connect calendar", "link calendar", "connect google calendar"]

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
    "date", "girlfriend", "boyfriend", "gf", "bf", "girl friend", "boy friend",
    "plans with", "invited me",
    "going out", "family dinner", "unexpected plans", "something came up",
    "can't make it", "need to cancel", "have to leave",
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

SCHEDULE_REQUEST_VERBS = [
    "schedule", "add", "book", "set up", "fit in", "squeeze in",
    "make time", "wanna", "want to", "need to",
    "pencil in", "slot in", "arrange",
]

SCHEDULABLE_ITEMS = [
    "lunch", "dinner", "breakfast", "coffee", "meeting", "call",
    "gym", "workout", "appointment", "session", "hangout",
    "catch up", "drinks", "brunch", "date", "outing",
]

CLEAR_SCHEDULE_KEYWORDS = [
    "clear my schedule", "cancel everything", "reschedule rest of day",
    "clear the rest", "cancel rest", "wipe my schedule", "cancel all",
    "move everything to tomorrow", "clear today", "start fresh today",
]
