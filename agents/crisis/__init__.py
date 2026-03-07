"""
Crisis Agent — PlanB Scheduling Assistant

Activated when mode == "crisis". Takes three emergency actions:
  1. Drops all tasks with priority score < 50 from today's schedule.
  2. Creates a 3-hour "PlanB: CRISIS - Do Not Disturb" Google Calendar block.
  3. Sends DND notice emails via AWS SES to attendees of cancelled meetings.
  4. Records every action in state["crisis_actions"].

LangGraph node — reads from and writes to PlanBState only.
"""

from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

import boto3

from state import PlanBState
from config.settings import SES_FROM_EMAIL, AWS_REGION
from utils.google_calendar import get_todays_events, create_event

DND_SUMMARY = "PlanB: CRISIS - Do Not Disturb"
LOW_PRIORITY_THRESHOLD = 50


def _create_dnd_block(phone: str = None) -> dict:
    """Create a 3-hour DND calendar block starting now. Returns the created event or {}."""
    now = datetime.now()
    start = now.isoformat() + "+05:30"
    end = (now + timedelta(hours=3)).isoformat() + "+05:30"
    return create_event(
        summary=DND_SUMMARY,
        start=start,
        end=end,
        metadata={"planb_type": "crisis_dnd"},
        phone=phone,
    )


def _send_dnd_email(ses_client, to_email: str, task_name: str) -> str:
    """Send a DND cancellation notice via SES. Returns 'sent' or 'failed'."""
    try:
        subject = f"Meeting Cancelled: {task_name}"
        body = (
            f"Hi,\n\n"
            f"I'm in an unplanned situation and have had to cancel today's meeting: {task_name}.\n\n"
            f"I will reach out to reschedule as soon as possible. Apologies for the short notice.\n\n"
            f"Thank you for your understanding."
        )
        ses_client.send_email(
            Source=SES_FROM_EMAIL,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        return "sent"
    except Exception as e:
        print(f"[Crisis] SES send failed for {to_email}: {e}")
        return "failed"


def crisis_agent(state: PlanBState) -> PlanBState:
    """Crisis Agent — triggered when mode == 'crisis'.

    Reads from state:
        task_scores (dict):  {event_id: int} from Priority Engine. Events with
                             score < 50 are dropped and their attendees notified.

    Writes to state:
        proposed_schedule (list): Tasks that survived the priority cutoff.
        crisis_actions (list):    Log of every action taken (drops, calendar block,
                                  DND emails).
    """
    try:
        task_scores = state.get("task_scores") or {}
        crisis_actions = []

        # STEP 1 — Load today's events and split by priority threshold
        user_phone = state.get("user_phone")
        events = get_todays_events(phone=user_phone)
        kept_schedule = []
        dropped_events = []

        for event in events:
            event_id = event.get("id", "")
            score = task_scores.get(event_id)
            if score is not None and score < LOW_PRIORITY_THRESHOLD:
                dropped_events.append(event)
                crisis_actions.append({
                    "action": "dropped",
                    "task_name": event.get("summary", "Unknown"),
                    "task_id": event_id,
                    "reason": f"Priority score {score} < {LOW_PRIORITY_THRESHOLD} during crisis",
                })
            else:
                kept_schedule.append({
                    "task_id": event_id,
                    "task_name": event.get("summary", "(No title)"),
                    "action": "keep",
                    "reason": "Retained during crisis — score above threshold or unscored.",
                    "old_time": event.get("start", ""),
                    "suggested_time": event.get("start", ""),
                })

        state["proposed_schedule"] = kept_schedule
        print(f"[Crisis] Dropped {len(dropped_events)} low-priority tasks, kept {len(kept_schedule)}.")

        # STEP 2 — Create 3-hour DND calendar block
        dnd_event = _create_dnd_block(phone=user_phone)
        if dnd_event.get("id"):
            crisis_actions.append({
                "action": "calendar_block_created",
                "summary": DND_SUMMARY,
                "start": dnd_event.get("start"),
                "end": dnd_event.get("end"),
            })
            print(f"[Crisis] DND block created: {dnd_event.get('start')} to {dnd_event.get('end')}")
        else:
            print("[Crisis] Warning: DND calendar block creation failed.")

        # STEP 3 — Send DND notice emails to attendees of dropped events
        if dropped_events and SES_FROM_EMAIL and AWS_REGION:
            ses_client = boto3.client("ses", region_name=AWS_REGION)
            for event in dropped_events:
                attendees = event.get("attendees") or []
                task_name = event.get("summary", "Meeting")
                for email_addr in attendees:
                    status = _send_dnd_email(ses_client, email_addr, task_name)
                    crisis_actions.append({
                        "action": "dnd_email",
                        "to": email_addr,
                        "meeting": task_name,
                        "status": status,
                    })
        elif not dropped_events:
            print("[Crisis] No low-priority tasks dropped — no DND emails needed.")

        state["crisis_actions"] = crisis_actions

    except Exception as e:
        print(f"[Crisis] Agent error: {e}")
        state["crisis_actions"] = state.get("crisis_actions") or []

    return state
