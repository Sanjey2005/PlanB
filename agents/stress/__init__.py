"""
Stress Agent — PlanB Scheduling Assistant

Activated when mode == "stress". Takes lighter-touch actions than the Crisis Agent:
  1. Lightens today's schedule by removing tasks with priority score < 40.
  2. Sets fatigue_level to "high" in state so downstream agents (Routine, etc.) adapt.
  3. Does NOT create calendar blocks.
  4. Does NOT send any emails.
  5. Records every action in state["stress_actions"].

LangGraph node — reads from and writes to PlanBState only.
"""

from dotenv import load_dotenv

load_dotenv()

from state import PlanBState
from utils.google_calendar import get_todays_events

LOW_PRIORITY_THRESHOLD = 40


def stress_agent(state: PlanBState) -> PlanBState:
    """Stress Agent — triggered when mode == 'stress'.

    Reads from state:
        task_scores (dict):  {event_id: int} from Priority Engine. Events with
                             score < 40 are lightened from the schedule.

    Writes to state:
        proposed_schedule (list): Tasks that survived the priority cutoff.
        fatigue_level (str):      Set to "high" so the Routine Agent is more
                                  conservative with habit blocks.
        stress_actions (list):    Log of every action taken (lightened tasks,
                                  fatigue adjustment).
    """
    try:
        task_scores = state.get("task_scores") or {}
        stress_actions = []

        # STEP 1 — Load today's events and split by priority threshold
        events = get_todays_events(phone=state.get("user_phone"))
        kept_schedule = []
        lightened_events = []

        for event in events:
            event_id = event.get("id", "")
            score = task_scores.get(event_id)
            if score is not None and score < LOW_PRIORITY_THRESHOLD:
                lightened_events.append(event)
                stress_actions.append({
                    "action": "lightened",
                    "task_name": event.get("summary", "Unknown"),
                    "task_id": event_id,
                    "reason": f"Priority score {score} < {LOW_PRIORITY_THRESHOLD} — giving you space today",
                })
            else:
                kept_schedule.append({
                    "task_id": event_id,
                    "task_name": event.get("summary", "(No title)"),
                    "action": "keep",
                    "reason": "Kept — important enough to hold during a tough day.",
                    "old_time": event.get("start", ""),
                    "suggested_time": event.get("start", ""),
                })

        state["proposed_schedule"] = kept_schedule
        print(f"[Stress] Lightened {len(lightened_events)} tasks, kept {len(kept_schedule)}.")

        # Advisory guard — propose stress actions without executing them
        delegation_depth = state.get("delegation_depth") or "assisted"
        if delegation_depth == "advisory":
            state["pending_proposals"] = [
                {"action": "lighten", "task_name": e.get("summary", "Unknown"),
                 "reason": f"Low priority ({task_scores.get(e.get('id', ''), '?')}) — would be lightened for stress relief"}
                for e in lightened_events
            ]
            state["awaiting_confirmation"] = True
            state["stress_actions"] = []
            return state

        # STEP 2 — Set fatigue_level to high so downstream agents adapt
        state["fatigue_level"] = "high"
        stress_actions.append({
            "action": "fatigue_set",
            "value": "high",
            "reason": "Stress mode raises fatigue level so routines and scoring protect your energy.",
        })

        state["stress_actions"] = stress_actions

    except Exception as e:
        print(f"[Stress] Agent error: {e}")
        state["stress_actions"] = state.get("stress_actions") or []

    return state
