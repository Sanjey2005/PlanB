"""
LangGraph Pipeline — PlanB Scheduling Assistant

Defines the full agent pipeline as a LangGraph StateGraph. Eleven specialised agents
are wired together with conditional routing controlled by the Orchestrator's decisions.

Pipeline flow:
  Monitor → Context → Resilience → Priority → Orchestrator
    → (conditional) Replan → Routine → Scheduler → (conditional) Negotiate → Comms → END
    → (conditional) Predictive Risk → Comms → END
    → (conditional) Comms → END

Scheduler can loop back to Orchestrator if schedule_conflict is detected.
"""

from dotenv import load_dotenv

load_dotenv()

import uuid
from datetime import datetime

from langgraph.graph import StateGraph, END

from agents.monitor import monitor_agent
from agents.context import context_agent
from agents.resilience import resilience_agent
from agents.orchestrator import orchestrator_agent
from agents.priority import priority_engine
from agents.replan import replan_agent
from agents.routine import routine_agent
from agents.scheduler import scheduler_agent
from agents.negotiate import negotiate_agent
from agents.comms import comms_agent
from agents.predictive import predictive_risk_agent
from agents.crisis import crisis_agent
from agents.stress import stress_agent
from agents.undo import undo_agent
from agents.lifestyle import lifestyle_agent
from agents.onboarding import onboarding_agent
from state import PlanBState, get_initial_state
from utils.s3_logger import log_pipeline_run
from utils.user_dna import get_user_dna, update_user_dna
from utils.keywords import IST_OFFSET
from utils.s3_logger import get_last_pipeline_run


# ---------------------------------------------------------------------------
# Conditional routing functions
# ---------------------------------------------------------------------------

def route_after_monitor(state: PlanBState) -> str:
    """Short-circuit to onboarding for brand-new users; otherwise run the full pipeline."""
    if state.get("mode") == "onboarding":
        return "onboarding"
    return "context"


def route_after_orchestrator(state: PlanBState) -> str:
    """Route after the Orchestrator based on which agents it selected to fire.

    - If 'replan' is in agents_to_fire → enter the replan branch.
    - Elif 'predictive_risk' is in agents_to_fire → run predictive risk analysis.
    - Elif 'lifestyle' is in agents_to_fire → run lifestyle agent.
    - Otherwise → skip straight to comms for a quick response.
    """
    agents = state.get("agents_to_fire") or []
    if "crisis" in agents:
        return "crisis"
    elif "undo" in agents:
        return "undo"
    elif "stress" in agents:
        return "stress"
    elif "replan" in agents:
        return "replan"
    elif "predictive_risk" in agents:
        return "predictive_risk"
    elif "lifestyle" in agents:
        return "lifestyle"
    elif "scheduler" in agents and "replan" not in agents:
        return "scheduler"
    else:
        return "comms"


def route_after_scheduler(state: PlanBState) -> str:
    """Route after the Scheduler based on conflicts and negotiation needs.

    - If schedule_conflict is True → loop back to Orchestrator for re-evaluation.
    - If 'negotiate' is in agents_to_fire → send reschedule emails.
    - Otherwise → proceed to comms.
    """

    agents = state.get("agents_to_fire") or []
    if "negotiate" in agents:
        return "negotiate"
    return "comms"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

graph = StateGraph(PlanBState)

# Add all agent nodes
graph.add_node("monitor", monitor_agent)
graph.add_node("context", context_agent)
graph.add_node("resilience", resilience_agent)
graph.add_node("orchestrator", orchestrator_agent)
graph.add_node("priority", priority_engine)
graph.add_node("replan", replan_agent)
graph.add_node("routine", routine_agent)
graph.add_node("scheduler", scheduler_agent)
graph.add_node("negotiate", negotiate_agent)
graph.add_node("comms", comms_agent)
graph.add_node("predictive_risk", predictive_risk_agent)
graph.add_node("crisis", crisis_agent)
graph.add_node("stress", stress_agent)
graph.add_node("undo", undo_agent)
graph.add_node("lifestyle", lifestyle_agent)
graph.add_node("onboarding", onboarding_agent)

# Entry point
graph.set_entry_point("monitor")

# Conditional edge after monitor — new users go straight to onboarding
graph.add_conditional_edges(
    "monitor",
    route_after_monitor,
    {
        "onboarding": "onboarding",
        "context": "context",
    },
)
graph.add_edge("context", "resilience")
graph.add_edge("resilience", "priority")
graph.add_edge("priority", "orchestrator")

# Conditional edge after orchestrator
graph.add_conditional_edges(
    "orchestrator",
    route_after_orchestrator,
    {
        "crisis": "crisis",
        "undo": "undo",
        "stress": "stress",
        "replan": "replan",
        "predictive_risk": "predictive_risk",
        "lifestyle": "lifestyle",
        "scheduler": "scheduler",
        "comms": "comms",
    },
)

# Replan branch
graph.add_edge("replan", "routine")
graph.add_edge("routine", "scheduler")

# Conditional edge after scheduler
graph.add_conditional_edges(
    "scheduler",
    route_after_scheduler,
    {
        "orchestrator": "orchestrator",
        "negotiate": "negotiate",
        "comms": "comms",
    },
)

# Terminal edges
graph.add_edge("negotiate", "comms")
graph.add_edge("predictive_risk", "comms")
graph.add_edge("crisis", "comms")
graph.add_edge("undo", "comms")
graph.add_edge("stress", "comms")
graph.add_edge("lifestyle", "comms")
graph.add_edge("onboarding", "comms")
graph.add_edge("comms", END)

# Compile
app = graph.compile()


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(initial_state: dict) -> dict:
    """Run the full PlanB agent pipeline.

    Takes an initial state dict (e.g. with disruption_raw, mode, user_phone),
    merges it into a clean PlanBState, invokes the compiled LangGraph, and
    logs the final result to S3 for audit.

    Args:
        initial_state: Dict with fields to seed into PlanBState before execution.

    Returns:
        Final state dict after all agents have run.
    """
    run_id = str(uuid.uuid4())
    state = get_initial_state()
    state.update(initial_state)

    # Inject current IST time so agents can distinguish past from future events
    now_ist = datetime.now(tz=IST_OFFSET)
    state["current_time"] = now_ist.isoformat()
    state["current_hour"] = now_ist.hour

    # Load User DNA profile before the pipeline runs so agents can use it
    user_phone = state.get("user_phone") or ""
    try:
        state["user_dna"] = get_user_dna(user_phone)
    except Exception as e:
        print(f"Pipeline: User DNA load failed: {e}")

    # Apply proposals mode — load pending proposals from last run and convert to proposed_schedule
    if state.get("mode") == "apply_proposals":
        try:
            last_run = get_last_pipeline_run(user_phone)
            pending = last_run.get("pending_proposals") or []
            proposed = []
            for p in pending:
                proposed.append({
                    "task_name": p.get("task_name", ""),
                    "task_id": p.get("task_id", ""),
                    "action": "move" if p.get("action") in ("move", "lighten") else p.get("action", "move"),
                    "old_time": p.get("old_time", ""),
                    "suggested_time": p.get("suggested_time", ""),
                    "reason": p.get("reason", ""),
                })
            state["proposed_schedule"] = proposed
            state["delegation_depth"] = "assisted"
            state["awaiting_confirmation"] = False
        except Exception as e:
            print(f"Pipeline: apply_proposals load failed: {e}")

    result = app.invoke(state)

    try:
        log_pipeline_run(dict(result), run_id)
    except Exception as e:
        print(f"Pipeline: S3 logging failed: {e}")

    # Persist learned updates to the User DNA profile after the pipeline completes
    try:
        update_user_dna(user_phone, dict(result))
    except Exception as e:
        print(f"Pipeline: User DNA update failed: {e}")

    return dict(result)
