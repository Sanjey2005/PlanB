import json

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_LARGE, GROQ_API_KEY
from state import PlanBState

load_dotenv()

CLASSIFICATION_PROMPT = """\
You are an AI scheduling assistant analyzing a disruption report.

Disruption: {disruption_raw}

Classify this disruption and return ONLY valid JSON with exactly these fields:
{{
  "disruption_type": one of [travel, health, calendar, work, external, none],
  "severity": one of [low, medium, high],
  "hours_impacted": float (estimated productive hours lost today, 0.0 if none),
  "fatigue_level": one of [none, low, medium, high],
  "context_summary": string (one clear sentence describing what happened),
  "tasks_likely_affected": list of strings (types of tasks probably blocked)
}}

Rules for fatigue_level detection:
- Keywords like headache, migraine, sick, unwell, nausea = high
- Keywords like tired, exhausted, drained, not feeling well = high
- Keywords like a bit tired, slightly off, not 100% = medium
- Keywords like stressed, anxious, overwhelmed = medium
- No health mention = none

Rules for severity:
- Lost 3+ hours OR missed critical deadline OR health emergency = high
- Lost 1-2 hours OR important meeting affected = medium
- Lost less than 1 hour OR minor inconvenience = low

Return ONLY the JSON object. No explanation. No markdown.\
"""

SAFE_DEFAULTS = {
    "disruption_type": "work",
    "severity": "medium",
    "hours_impacted": 1.0,
    "fatigue_level": "none",
    "context_summary": "",
    "tasks_likely_affected": [],
}


def _parse_llm_json(content: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def context_agent(state: PlanBState) -> PlanBState:
    """Context Agent — classifies the disruption type, severity, and user fatigue.

    Uses Groq llama-3.3-70b-versatile to read the raw disruption text and produce
    a structured classification that downstream agents (Resilience, Orchestrator,
    Priority Engine, Routine) rely on for their decisions.

    Reads from state:
        disruption_raw (str): Free-text description of the disruption (from Monitor).

    Writes to state:
        disruption_type (str):  travel / health / calendar / work / external / none.
        severity (str):         low / medium / high.
        hours_impacted (float): Estimated productive hours lost today.
        fatigue_level (str):    none / low / medium / high.
        context_summary (str):  One-sentence human-readable summary.
        mode (str):             Set to 'disruption' if not already set.
    """
    disruption_raw = state.get("disruption_raw")
    if not disruption_raw:
        return state

    # Build safe defaults with the actual raw text as fallback summary
    defaults = {**SAFE_DEFAULTS, "context_summary": disruption_raw}

    try:
        llm = ChatGroq(model=GROQ_MODEL_LARGE, api_key=GROQ_API_KEY)
        prompt = CLASSIFICATION_PROMPT.format(disruption_raw=disruption_raw)
        response = llm.invoke(prompt)

        try:
            result = _parse_llm_json(response.content)
        except (json.JSONDecodeError, IndexError) as e:
            print(f"Context Agent: failed to parse Groq JSON, using defaults: {e}")
            result = defaults

    except Exception as e:
        print(f"Context Agent: Groq call failed, using defaults: {e}")
        result = defaults

    state["disruption_type"] = result.get("disruption_type", defaults["disruption_type"])
    state["severity"] = result.get("severity", defaults["severity"])
    state["hours_impacted"] = result.get("hours_impacted", defaults["hours_impacted"])
    state["fatigue_level"] = result.get("fatigue_level", defaults["fatigue_level"])
    state["context_summary"] = result.get("context_summary", defaults["context_summary"])

    if not state.get("mode"):
        state["mode"] = "disruption"

    return state
