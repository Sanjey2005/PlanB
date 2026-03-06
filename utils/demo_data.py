"""
Demo data for the /demo endpoint.

Provides a realistic fake calendar, demo user, and disruption scenario
so the pipeline can run end-to-end without touching any live APIs.
"""

DEMO_USER_PHONE = "demo_user"

DEMO_DISRUPTION = "My flight got delayed by 2 hours"

# Today = 2026-03-06, all times IST (UTC+5:30)
DEMO_CALENDAR = [
    {
        "id": "event_deepwork_001",
        "summary": "Deep Work — Product Design",
        "start": "2026-03-06T09:00:00+05:30",
        "end": "2026-03-06T11:00:00+05:30",
        "attendees": [],
        "description": "Focused design session for NeoVerse hackathon product",
    },
    {
        "id": "event_clientcall_002",
        "summary": "Client Call — NeoVerse Demo",
        "start": "2026-03-06T14:00:00+05:30",
        "end": "2026-03-06T15:00:00+05:30",
        "attendees": [
            {"email": "alex.chen@neoverse.io", "displayName": "Alex Chen"},
        ],
        "description": "Live product demo for NeoVerse investor",
    },
    {
        "id": "event_standup_003",
        "summary": "Team Standup",
        "start": "2026-03-06T15:00:00+05:30",
        "end": "2026-03-06T15:30:00+05:30",
        "attendees": [
            {"email": "priya@teamplanb.io", "displayName": "Priya Sharma"},
            {"email": "rohan@teamplanb.io", "displayName": "Rohan Mehta"},
        ],
        "description": "Daily team sync",
    },
    {
        "id": "event_gym_004",
        "summary": "Gym",
        "start": "2026-03-06T18:00:00+05:30",
        "end": "2026-03-06T19:00:00+05:30",
        "attendees": [],
        "description": "Personal fitness",
    },
]

# Events returned by get_events_range(2) — next two days
DEMO_TOMORROW_EVENTS = [
    {
        "id": "event_review_005",
        "summary": "Weekly Review",
        "start": "2026-03-07T10:00:00+05:30",
        "end": "2026-03-07T11:00:00+05:30",
        "attendees": [],
        "description": "Weekly progress review",
    },
    {
        "id": "event_mentor_006",
        "summary": "Lunch with Mentor",
        "start": "2026-03-07T13:00:00+05:30",
        "end": "2026-03-07T14:00:00+05:30",
        "attendees": [
            {"email": "dr.nair@accelerator.in", "displayName": "Dr. Nair"},
        ],
        "description": "Startup mentoring session",
    },
]

# Free slots returned by get_free_slots() — afternoon/evening windows after disruption
DEMO_FREE_SLOTS = [
    {"start": "2026-03-06T16:30:00+05:30", "end": "2026-03-06T17:30:00+05:30"},
    {"start": "2026-03-06T17:30:00+05:30", "end": "2026-03-06T18:30:00+05:30"},
    {"start": "2026-03-06T19:30:00+05:30", "end": "2026-03-06T20:30:00+05:30"},
]
