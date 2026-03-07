from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_LARGE, GROQ_API_KEY
from state import PlanBState

load_dotenv()

INTENT_PROMPT = """\
The user sent this message: '{disruption_raw}'
This is an on-demand request (not a disruption).
Classify the intent as exactly one of:
- add_task: user wants to add a new task or event
- setup_routine: user wants to set up a recurring habit
- reschedule: user wants to manually move something
- query: user is asking a question
Reply with ONLY the intent word.\
"""

INTENT_TO_AGENTS = {
    "add_task": ["scheduler", "comms"],
    "setup_routine": ["routine", "scheduler", "comms"],
    "reschedule": ["replan", "scheduler", "comms"],
    "query": ["comms"],
}
DEFAULT_ON_DEMAND_AGENTS = ["scheduler", "comms"]


def _detect_delegation_depth(raw: str) -> str:
    """Detect delegation depth from user keywords in the disruption text."""
    text = (raw or "").lower()
    if "autonomous mode" in text or "full auto" in text or "just do it" in text:
        return "autonomous"
    if "advisory mode" in text or "just suggest" in text or "ask me" in text:
        return "advisory"
    return "assisted"


def _classify_on_demand_intent(raw: str) -> list:
    """Call Groq to classify on-demand intent and return the agent list."""
    try:
        llm = ChatGroq(model=GROQ_MODEL_LARGE, api_key=GROQ_API_KEY)
        prompt = INTENT_PROMPT.format(disruption_raw=raw)
        response = llm.invoke(prompt)
        intent = response.content.strip().lower()
        return INTENT_TO_AGENTS.get(intent, DEFAULT_ON_DEMAND_AGENTS)
    except Exception as e:
        print(f"Orchestrator: Groq intent classification failed, using default: {e}")
        return DEFAULT_ON_DEMAND_AGENTS


def _build_disruption_agents(cascade_severity: str, severity: str, disruption_type: str) -> list:
    """Build the agent list for disruption mode based on severity and type."""
    agents = ["replan", "routine", "scheduler", "comms"]

    # High cascade → add negotiate before comms
    if cascade_severity == "high":
        comms_idx = agents.index("comms")
        agents.insert(comms_idx, "negotiate")

    # Minor disruption → skip routine adjustment
    if cascade_severity == "low" and severity == "low":
        if "routine" in agents:
            agents.remove("routine")

    # Health disruptions always affect routines
    if disruption_type == "health":
        if "routine" not in agents:
            scheduler_idx = agents.index("scheduler")
            agents.insert(scheduler_idx, "routine")

    return agents


def orchestrator_agent(state: PlanBState) -> PlanBState:
    """Orchestrator Agent — the brain of PlanB's runtime routing.

    Reads the full situation from state and makes two key decisions:

    1. delegation_depth: How autonomous PlanB should act.
       - 'autonomous': User said "just do it" / "full auto" — act without asking.
       - 'advisory':   User said "just suggest" / "ask me" — only propose changes.
       - 'assisted':   Default — make changes but explain what happened.

    2. agents_to_fire: Which agents should run next and in what order.
       The pipeline is NEVER fixed — it adapts at runtime:

       morning_briefing / evening_review → [predictive_risk, comms]
       query                             → [comms]
       on_demand                         → depends on Groq intent classification
       disruption                        → [replan, routine?, scheduler, negotiate?, comms]
         - High cascade adds negotiate (reschedule emails needed)
         - Low+low removes routine (not worth touching habits)
         - Health always keeps routine (affects energy / habits)

    Reads from state:
        mode, cascade_severity, severity, disruption_type, disruption_raw.

    Writes to state:
        agents_to_fire (list):    Ordered list of agent names to execute.
        delegation_depth (str):   autonomous / assisted / advisory.
    """
    try:
        mode = state.get("mode") or "disruption"
        cascade_severity = state.get("cascade_severity") or "low"
        severity = state.get("severity") or "low"
        disruption_type = state.get("disruption_type") or "work"
        disruption_raw = state.get("disruption_raw") or ""

        # STEP 2 — Delegation depth
        state["delegation_depth"] = _detect_delegation_depth(disruption_raw)

        # STEP 3 — Determine agents to fire
        if mode == "crisis":
            agents_to_fire = ["crisis", "comms"]

        elif mode == "undo":
            agents_to_fire = ["undo", "comms"]

        elif mode == "stress":
            agents_to_fire = ["stress", "comms"]

        elif mode in ("morning_briefing", "evening_review"):
            agents_to_fire = ["predictive_risk", "comms"]

        elif mode == "query":
            agents_to_fire = ["comms"]

        elif mode == "lifestyle":
            agents_to_fire = ["lifestyle", "comms"]

        elif mode == "on_demand":
            agents_to_fire = _classify_on_demand_intent(disruption_raw)

        elif mode == "disruption":
            agents_to_fire = _build_disruption_agents(cascade_severity, severity, disruption_type)

        else:
            agents_to_fire = ["comms"]

        state["agents_to_fire"] = agents_to_fire

        # Transparent reasoning — explain why this routing was chosen
        depth = state["delegation_depth"]
        state["decision_reasoning"] = (
            f"Mode: {mode}. Severity: {severity}. Cascade: {cascade_severity}. "
            f"Delegation: {depth}. Agents fired: {', '.join(agents_to_fire)}."
        )

        return state

    except Exception as e:
        print(f"Orchestrator: unexpected error: {e}")
        state["agents_to_fire"] = ["comms"]
        state["delegation_depth"] = "assisted"
        return state
