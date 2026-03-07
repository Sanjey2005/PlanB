"""
Seed script — populate Google Calendar with a realistic knowledge-worker week.

Usage:
    python add_sample_events.py           # add all sample events
    python add_sample_events.py --clear   # remove previously seeded events, then re-add
    python add_sample_events.py --remove  # remove previously seeded events only

All created events are tagged with extendedProperties.private.planb_seeded = "true"
so they can be found and removed cleanly by --clear / --remove.
"""

import argparse
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from utils.google_calendar import build_service, create_event

TIMEZONE = "Asia/Kolkata"
IST = "+05:30"

TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _day(offset: int, hour: int, minute: int = 0) -> str:
    """Return ISO 8601 datetime string for TODAY + offset days at HH:MM IST."""
    dt = TODAY + timedelta(days=offset, hours=hour, minutes=minute)
    return dt.isoformat() + IST


def _this_weekday(weekday: int, hour: int, minute: int = 0) -> str:
    """Return ISO string for the nearest occurrence of weekday (0=Mon) at HH:MM."""
    days_ahead = weekday - TODAY.weekday()
    if days_ahead < 0:
        days_ahead += 7
    dt = TODAY + timedelta(days=days_ahead, hours=hour, minutes=minute)
    return dt.isoformat() + IST


# ── Event definitions ─────────────────────────────────────────────────────────

EVENTS = [
    # ── Routines (recurring, tagged planb_task_type: routine) ────────────────
    {
        "summary": "Morning Meditation",
        "start": _day(0, 7, 0),
        "end": _day(0, 7, 30),
        "recurrence": "RRULE:FREQ=DAILY",
        "metadata": {
            "planb_task_type": "routine",
            "planb_negotiable": "true",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "Gym Session",
        "start": _this_weekday(0, 18, 30),  # Mon/Wed/Fri at 6:30 PM
        "end": _this_weekday(0, 19, 30),
        "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR",
        "metadata": {
            "planb_task_type": "routine",
            "planb_negotiable": "true",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "Reading Time",
        "start": _day(0, 21, 30),
        "end": _day(0, 22, 0),
        "recurrence": "RRULE:FREQ=DAILY",
        "metadata": {
            "planb_task_type": "routine",
            "planb_negotiable": "true",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "Lunch Break",
        "start": _day(0, 13, 0),
        "end": _day(0, 14, 0),
        "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        "metadata": {
            "planb_task_type": "routine",
            "planb_negotiable": "false",
            "planb_seeded": "true",
        },
    },

    # ── Work events ──────────────────────────────────────────────────────────
    {
        "summary": "Daily Standup",
        "start": _day(0, 9, 30),
        "end": _day(0, 9, 45),
        "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        "metadata": {
            "planb_task_type": "meeting",
            "planb_negotiable": "false",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "Deep Work Block",
        "start": _day(0, 10, 0),
        "end": _day(0, 12, 30),
        "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH",
        "metadata": {
            "planb_task_type": "focus",
            "planb_negotiable": "false",
            "planb_priority": "high",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "Client Call — Acme Corp",
        "start": _day(0, 14, 0),
        "end": _day(0, 15, 0),
        "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=MO,WE",
        "metadata": {
            "planb_task_type": "meeting",
            "planb_negotiable": "true",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "1:1 with Manager",
        "start": _this_weekday(0, 15, 0),   # Monday
        "end": _this_weekday(0, 15, 45),
        "recurrence": None,
        "metadata": {
            "planb_task_type": "meeting",
            "planb_negotiable": "false",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "Product Review",
        "start": _this_weekday(2, 16, 0),   # Wednesday
        "end": _this_weekday(2, 17, 0),
        "recurrence": None,
        "metadata": {
            "planb_task_type": "meeting",
            "planb_negotiable": "true",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "DEADLINE: Submit Q1 Report",
        "start": _this_weekday(3, 17, 0),   # Thursday
        "end": _this_weekday(3, 18, 0),
        "recurrence": None,
        "metadata": {
            "planb_task_type": "work_deliverable",
            "planb_negotiable": "false",
            "planb_priority": "critical",
            "planb_seeded": "true",
        },
    },

    # ── Personal events ──────────────────────────────────────────────────────
    {
        "summary": "Dinner with Priya",
        "start": _this_weekday(1, 19, 30),  # Tuesday
        "end": _this_weekday(1, 21, 30),
        "recurrence": None,
        "metadata": {
            "planb_task_type": "personal",
            "planb_negotiable": "false",
            "planb_seeded": "true",
        },
    },
    {
        "summary": "Doctor Appointment",
        "start": _this_weekday(4, 11, 0),   # Friday
        "end": _this_weekday(4, 12, 0),
        "recurrence": None,
        "metadata": {
            "planb_task_type": "personal",
            "planb_negotiable": "false",
            "planb_priority": "high",
            "planb_seeded": "true",
        },
    },
]


def _find_seeded_events(service) -> list:
    """Return event IDs for all events tagged planb_seeded=true."""
    ids = []
    try:
        now = datetime.now()
        start = (now - timedelta(days=7)).isoformat() + IST
        end = (now + timedelta(days=30)).isoformat() + IST
        page_token = None
        while True:
            resp = service.events().list(
                calendarId="primary",
                timeMin=start,
                timeMax=end,
                singleEvents=False,
                maxResults=250,
                pageToken=page_token,
            ).execute()
            for e in resp.get("items", []):
                private = e.get("extendedProperties", {}).get("private", {})
                if private.get("planb_seeded") == "true":
                    ids.append(e["id"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as ex:
        print(f"[Seed] Warning — could not search existing events: {ex}")
    return ids


def _remove_events(service, event_ids: list) -> int:
    removed = 0
    for eid in event_ids:
        try:
            service.events().delete(calendarId="primary", eventId=eid).execute()
            removed += 1
        except Exception as ex:
            print(f"[Seed] Could not delete {eid}: {ex}")
    return removed


def main():
    parser = argparse.ArgumentParser(description="Seed Google Calendar with PlanB demo events.")
    parser.add_argument("--clear", action="store_true", help="Remove existing seeded events then re-add.")
    parser.add_argument("--remove", action="store_true", help="Remove existing seeded events only.")
    args = parser.parse_args()

    print("[Seed] Authenticating with Google Calendar...")
    try:
        service = build_service()
    except Exception as e:
        print(f"[Seed] Auth failed: {e}")
        sys.exit(1)

    if args.clear or args.remove:
        print("[Seed] Searching for previously seeded events...")
        old_ids = _find_seeded_events(service)
        if old_ids:
            n = _remove_events(service, old_ids)
            print(f"[Seed] Removed {n} previously seeded event(s).")
        else:
            print("[Seed] No previously seeded events found.")
        if args.remove:
            print("[Seed] Done (remove only).")
            return

    print(f"[Seed] Creating {len(EVENTS)} events...\n")
    created = 0
    for ev in EVENTS:
        result = create_event(
            summary=ev["summary"],
            start=ev["start"],
            end=ev["end"],
            metadata=ev.get("metadata"),
            recurrence=ev.get("recurrence"),
        )
        if result:
            task_type = (ev.get("metadata") or {}).get("planb_task_type", "event")
            recurring = "(recurring)" if ev.get("recurrence") else ""
            print(f"  + [{task_type:16s}] {ev['summary']} {recurring}")
            created += 1
        else:
            print(f"  ! FAILED: {ev['summary']}")

    print(f"\n[Seed] Done. {created}/{len(EVENTS)} events created.")
    print("       Run with --clear to reset and re-seed.")


if __name__ == "__main__":
    main()
