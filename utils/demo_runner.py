"""
Demo pipeline runner for the /demo endpoint.

Runs the full LangGraph agent pipeline against a pre-scripted disruption
scenario with all live API dependencies mocked out:
  - Google Calendar (get_todays_events, get_events_range, update_event_time, get_free_slots)
  - WhatsApp (send_message)
  - AWS SES in Negotiate agent (boto3.client)
  - S3 audit logger (log_pipeline_run)
  - User DNA S3 reads/writes (get_user_dna, update_user_dna)

No real credentials are touched. LLM calls (Groq) go through as normal.
"""

import contextlib
import time
from unittest.mock import MagicMock, patch

from dotenv import load_dotenv

load_dotenv()

from graph import app as graph_app
from state import get_initial_state
from utils.demo_data import (
    DEMO_CALENDAR,
    DEMO_DISRUPTION,
    DEMO_FREE_SLOTS,
    DEMO_TOMORROW_EVENTS,
    DEMO_USER_PHONE,
)

# Realistic demo DNA — represents a returning user with established habits
_DEMO_DNA = {
    "user_phone": DEMO_USER_PHONE,
    "protected_habits": ["Gym"],
    "peak_hours": ["9AM-12PM"],
    "never_reschedule": [],
    "preferred_meeting_window": "2PM-5PM",
    "avg_fatigue_pattern": {"last_fatigue": "low"},
    "learned_overrides": {"Client Call — NeoVerse Demo": 2},
    "crisis_contacts": [],
    "streak_records": {"Gym": {"drop_count": 0, "kept_streak": 5}},
    "total_pipeline_runs": 12,
    "last_updated": "2026-03-05T20:00:00",
}


def run_demo_pipeline() -> dict:
    """Run the full PlanB agent pipeline on a pre-scripted flight-delay disruption.

    Mocks all live API dependencies (Calendar, WhatsApp, S3, SES, User DNA) and
    runs the compiled LangGraph graph, streaming node executions to capture which
    agents fired.

    Returns:
        {
            "status": "ok",
            "whatsapp_message": str,
            "decision_log": dict,
            "agents_fired": list[str],
            "pipeline_duration_ms": int,
        }
    """
    captured_messages: list[str] = []

    def _mock_send_message(to: str, message: str) -> dict:
        captured_messages.append(message)
        return {"messages_sent": 1}

    initial_state = get_initial_state()
    initial_state["disruption_raw"] = DEMO_DISRUPTION
    initial_state["disruption_source"] = "user_message"
    initial_state["mode"] = "disruption"
    initial_state["user_phone"] = DEMO_USER_PHONE

    patches = [
        # Priority agent — module-level imports
        patch("agents.priority.get_todays_events", return_value=DEMO_CALENDAR),
        patch("agents.priority.get_events_range", return_value=DEMO_TOMORROW_EVENTS),
        patch("agents.priority.get_learned_scores", return_value={}),
        # Resilience agent
        patch("agents.resilience.get_events_range", return_value=DEMO_TOMORROW_EVENTS),
        # Replan agent
        patch("agents.replan.get_todays_events", return_value=DEMO_CALENDAR),
        # Routine agent
        patch("agents.routine.get_todays_events", return_value=DEMO_CALENDAR),
        patch("agents.routine.get_drop_count_last_n_days", return_value=0),
        # Scheduler agent
        patch("agents.scheduler.get_todays_events", return_value=DEMO_CALENDAR),
        patch("agents.scheduler.get_events_range", return_value=DEMO_TOMORROW_EVENTS),
        patch("agents.scheduler.update_event_time", return_value=True),
        patch("agents.scheduler.get_free_slots", return_value=DEMO_FREE_SLOTS),
        # Comms agent — WhatsApp send
        patch("agents.comms.send_message", side_effect=_mock_send_message),
        # Source modules — covers any local/runtime imports (e.g., inside comms functions)
        patch("utils.google_calendar.get_todays_events", return_value=DEMO_CALENDAR),
        patch("utils.google_calendar.get_events_range", return_value=DEMO_TOMORROW_EVENTS),
        patch("utils.google_calendar.update_event_time", return_value=True),
        # Negotiate agent — prevent real SES boto3 calls
        patch("agents.negotiate.boto3.client", return_value=MagicMock()),
        # S3 audit logger
        patch("graph.log_pipeline_run", return_value="demo/mock-run"),
        # User DNA — return demo profile, discard updates (no S3 writes)
        patch("graph.get_user_dna", return_value=dict(_DEMO_DNA)),
        patch("graph.update_user_dna", return_value=None),
    ]

    agents_fired: list[str] = []
    final_state: dict = dict(initial_state)
    error_info: str | None = None

    start_ms = time.time() * 1000

    try:
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            for chunk in graph_app.stream(initial_state):
                for node_name, node_updates in chunk.items():
                    agents_fired.append(node_name)
                    if isinstance(node_updates, dict):
                        final_state.update(node_updates)

    except Exception as exc:
        error_info = str(exc)
        print(f"[Demo] Pipeline error: {exc}")

    duration_ms = int(time.time() * 1000 - start_ms)

    whatsapp_message = (
        captured_messages[0]
        if captured_messages
        else final_state.get("whatsapp_message") or ""
    )

    decision_log = {
        "disruption": DEMO_DISRUPTION,
        "mode": final_state.get("mode"),
        "severity": final_state.get("severity"),
        "hours_impacted": final_state.get("hours_impacted"),
        "context_summary": final_state.get("context_summary"),
        "agents_selected": final_state.get("agents_to_fire"),
        "delegation_depth": final_state.get("delegation_depth"),
        "decision_reasoning": final_state.get("decision_reasoning"),
        "cascade_severity": final_state.get("cascade_severity"),
        "deadline_risks": final_state.get("deadline_risks"),
        "proposed_schedule": final_state.get("proposed_schedule"),
        "confirmed_schedule": final_state.get("confirmed_schedule"),
        "confidence_scores": final_state.get("confidence_scores"),
        "routine_decisions": final_state.get("routine_decisions"),
        "emails_sent": final_state.get("emails_sent"),
    }

    result = {
        "status": "ok" if not error_info else "error",
        "whatsapp_message": whatsapp_message,
        "decision_log": decision_log,
        "agents_fired": agents_fired,
        "pipeline_duration_ms": duration_ms,
    }

    if error_info:
        result["error"] = error_info

    return result
