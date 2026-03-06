from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from config.settings import GROQ_MODEL_FAST, GROQ_API_KEY
from state import PlanBState
from utils.google_calendar import (
    get_todays_events,
    get_events_range,
    update_event_time,
    get_free_slots,
)

load_dotenv()

IST_OFFSET = timezone(timedelta(hours=5, minutes=30))
IST_OFFSET_STR = "+05:30"


def _parse_datetime(dt_str: str) -> datetime | None:
    """Parse an ISO 8601 datetime string (with or without offset) to a datetime."""
    if not dt_str:
        return None
    try:
        import re
        normalised = re.sub(r"[+-]\d{2}:\d{2}$", "", dt_str.strip())
        return datetime.fromisoformat(normalised)
    except Exception:
        return None


def _duration_minutes(start_str: str, end_str: str) -> int:
    """Return event duration in minutes, defaulting to 60 if unparseable."""
    start = _parse_datetime(start_str)
    end = _parse_datetime(end_str)
    if start and end and end > start:
        return int((end - start).total_seconds() / 60)
    return 60


def _build_event_lookup(events: list) -> dict:
    """Return {event_id: event_dict} from a list of calendar events."""
    return {e["id"]: e for e in events if e.get("id")}


def _pick_slot(
    date_str: str,
    duration: int,
    claimed_slots: set,
) -> dict | None:
    """Return first free slot on date_str that hasn't been claimed yet.

    claimed_slots is a set of start ISO strings already reserved this run.
    """
    slots = get_free_slots(date_str, duration)
    for slot in slots:
        if slot["start"] not in claimed_slots:
            return slot
    return None


def _confidence(slot: dict | None, pushed_to_tomorrow: bool) -> int:
    """Estimate a scheduling confidence score 0-100."""
    if slot is None:
        return 0
    if pushed_to_tomorrow:
        return 65
    # Heuristic: if start is within 2 hours of the original suggested time, tight fit
    return 90


def scheduler_agent(state: PlanBState) -> PlanBState:
    """Scheduler Agent — the ONLY agent that writes to Google Calendar.

    Takes the proposed_schedule from the Replan Agent and converts it into
    confirmed calendar changes by finding real free slots and calling
    update_event_time() for each task that needs to move.

    Reads from state:
        proposed_schedule (list):  [{task_id, task_name, action, reason,
                                     old_time, suggested_time}]
        task_scores (dict):        {event_id: int} for tiebreaking.

    Writes to state:
        confirmed_schedule (list): [{task_id, task_name, old_time, new_time,
                                     confidence, moved_to_tomorrow}]
        moved_meetings (list):     Subset of confirmed entries where attendees exist.
        confidence_scores (dict):  {task_name: confidence_int}
        schedule_conflict (bool):  True if any task could not be placed.
    """
    try:
        proposed = state.get("proposed_schedule")
        if not proposed:
            return state

        tasks_to_move = [t for t in proposed if t.get("action") == "move"]
        if not tasks_to_move:
            state["confirmed_schedule"] = []
            state["moved_meetings"] = []
            state["confidence_scores"] = {}
            state["schedule_conflict"] = False
            return state

        task_scores = state.get("task_scores") or {}

        # Build event lookup from today + next 2 days
        all_events = get_todays_events() + get_events_range(2)
        event_lookup = _build_event_lookup(all_events)

        today = datetime.now(tz=IST_OFFSET).date()
        tomorrow = today + timedelta(days=1)
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")

        # Sort tasks by priority score descending so higher-priority tasks claim slots first
        tasks_to_move.sort(
            key=lambda t: task_scores.get(t.get("task_id", ""), 50),
            reverse=True,
        )

        confirmed_schedule = []
        moved_meetings = []
        confidence_scores = {}
        claimed_slots: set = set()   # track start-times already reserved this run
        any_unplaced = False

        for task in tasks_to_move:
            task_id = task.get("task_id", "")
            task_name = task.get("task_name", "Unknown task")
            old_time = task.get("old_time", "")
            suggested_time = task.get("suggested_time", "")

            # Determine duration from calendar event
            event = event_lookup.get(task_id)
            if event:
                duration = _duration_minutes(event.get("start", ""), event.get("end", ""))
            else:
                duration = 60

            # Decide which date to try first
            suggested_dt = _parse_datetime(suggested_time)
            if suggested_dt and suggested_dt.date() == today:
                first_date, second_date = today_str, tomorrow_str
            else:
                first_date, second_date = today_str, tomorrow_str

            # Find a slot
            slot = _pick_slot(first_date, duration, claimed_slots)
            pushed_to_tomorrow = False
            if slot is None:
                slot = _pick_slot(second_date, duration, claimed_slots)
                pushed_to_tomorrow = slot is not None

            confidence = _confidence(slot, pushed_to_tomorrow)

            if slot:
                new_start = slot["start"]
                new_end = slot["end"]
                claimed_slots.add(new_start)

                # Write to Google Calendar
                try:
                    update_event_time(task_id, new_start, new_end)
                except Exception as e:
                    print(f"Scheduler Agent: failed to update event '{task_name}': {e}")
                    confidence = 0

                confirmed_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "old_time": old_time,
                    "new_time": new_start,
                    "confidence": confidence,
                    "moved_to_tomorrow": pushed_to_tomorrow,
                }
                confirmed_schedule.append(confirmed_entry)
                confidence_scores[task_name] = confidence

                # Flag as moved_meeting if the event has attendees
                if event and event.get("attendees"):
                    moved_meetings.append(confirmed_entry)

            else:
                # No slot found anywhere
                any_unplaced = True
                confirmed_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "old_time": old_time,
                    "new_time": None,
                    "confidence": 0,
                    "moved_to_tomorrow": False,
                }
                confirmed_schedule.append(confirmed_entry)
                confidence_scores[task_name] = 0
                print(f"Scheduler Agent: no slot found for '{task_name}'")

        state["confirmed_schedule"] = confirmed_schedule
        state["moved_meetings"] = moved_meetings
        state["confidence_scores"] = confidence_scores
        state["schedule_conflict"] = any_unplaced
        return state

    except Exception as e:
        print(f"Scheduler Agent: unexpected error: {e}")
        state["confirmed_schedule"] = []
        state["moved_meetings"] = []
        state["confidence_scores"] = {}
        state["schedule_conflict"] = True
        return state
