"""
Undo Agent — PlanB Scheduling Assistant

Activated when mode == "undo". Reverts the last confirmed schedule change by:
  1. Fetching the most recent completed pipeline run from S3 logs.
  2. Reading confirmed_schedule from that log.
  3. Calling update_event_time() on each entry that has both old_time and new_time,
     moving the event back to its original slot.
  4. Recording the result in state["undo_result"].

LangGraph node — reads from and writes to PlanBState only.
"""

import re
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from state import PlanBState
from utils.s3_logger import get_last_pipeline_run
from utils.google_calendar import update_event_time
from utils.keywords import IST_OFFSET_STR


def _compute_old_end(old_start: str, new_start: str, new_end: str) -> str:
    """Compute old_end preserving the original event duration.

    Duration = new_end - new_start. Applies that to old_start.
    Falls back to old_start + 1 hour if times cannot be parsed.
    """
    try:
        strip = lambda s: re.sub(r"[+-]\d{2}:\d{2}$", "", s.strip())
        ns = datetime.fromisoformat(strip(new_start))
        ne = datetime.fromisoformat(strip(new_end))
        duration = ne - ns
        os_ = datetime.fromisoformat(strip(old_start))
        return (os_ + duration).isoformat() + IST_OFFSET_STR
    except Exception:
        try:
            strip = lambda s: re.sub(r"[+-]\d{2}:\d{2}$", "", s.strip())
            os_ = datetime.fromisoformat(strip(old_start))
            return (os_ + timedelta(hours=1)).isoformat() + IST_OFFSET_STR
        except Exception:
            return old_start


def undo_agent(state: PlanBState) -> PlanBState:
    """Undo Agent — triggered when mode == 'undo'.

    Reads from state:
        user_phone (str): Identifies the user's logs in S3.

    Writes to state:
        undo_result (dict): {
            "reverted": [{"task_name": str, "reverted_to": str}, ...],
            "from_run": str | None,
        }
    """
    try:
        user_phone = state.get("user_phone") or ""

        # STEP 1 — Fetch the last completed pipeline run from S3
        last_run = get_last_pipeline_run(user_phone)
        if not last_run:
            state["undo_result"] = {"reverted": [], "from_run": None}
            print("[Undo] No previous completed run found in S3.")
            return state

        from_run = last_run.get("_run_id", "unknown")

        # STEP 2 — Find tasks that were moved (have both old_time and new_time)
        confirmed = last_run.get("confirmed_schedule") or []
        moveable = [
            t for t in confirmed
            if t.get("task_id") and t.get("old_time") and t.get("new_time")
        ]

        if not moveable:
            state["undo_result"] = {"reverted": [], "from_run": from_run}
            print("[Undo] No moveable tasks found in last run's confirmed_schedule.")
            return state

        # STEP 3 — Revert each task to its old_time
        reverted = []
        for task in moveable:
            task_id = task["task_id"]
            task_name = task.get("task_name", "Unknown")
            old_start = task["old_time"]
            new_start = task["new_time"]
            # new_end not stored; fall back to old_start + 1h via _compute_old_end
            old_end = _compute_old_end(old_start, new_start, new_start)

            try:
                result = update_event_time(task_id, old_start, old_end, phone=user_phone)
                if result:
                    reverted.append({"task_name": task_name, "reverted_to": old_start})
                    print(f"[Undo] Reverted '{task_name}' to {old_start}")
                else:
                    print(f"[Undo] update_event_time returned empty for '{task_name}'")
            except Exception as e:
                print(f"[Undo] Error reverting '{task_name}': {e}")

        # STEP 4 — Write result to state
        state["undo_result"] = {"reverted": reverted, "from_run": from_run}
        print(f"[Undo] Reverted {len(reverted)} task(s) from run {from_run}")

    except Exception as e:
        print(f"[Undo] Agent error: {e}")
        state["undo_result"] = state.get("undo_result") or {"reverted": [], "from_run": None}

    return state
