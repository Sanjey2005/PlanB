import json
from datetime import datetime

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_LARGE, GROQ_API_KEY
from state import PlanBState
from utils.google_calendar import get_events_range

load_dotenv()

CASCADE_PROMPT = """\
You are an expert scheduling analyst. A disruption has occurred.

Disruption type: {disruption_type}
Severity: {severity}
Hours lost today: {hours_impacted}
Summary: {context_summary}

Upcoming schedule (next 3 days):
{schedule_string}

Analyze the full cascade impact of this disruption and return ONLY valid JSON:
{{
  "directly_blocked": [list of event summaries that CANNOT happen due to this disruption],
  "indirectly_affected": [list of event summaries whose timing or feasibility is impacted],
  "deadline_risks": [
    {{
      "task": string (event summary),
      "deadline": string (date or time),
      "status": one of [SAFE, AT RISK, CRITICAL],
      "reason": string (why it is at risk),
      "recovery": string (specific action needed to protect this deadline)
    }}
  ],
  "cascade_severity": one of [low, medium, high],
  "cascade_summary": string (2 sentences describing the overall impact)
}}

cascade_severity rules:
- high: any CRITICAL deadline risk OR 3+ directly blocked tasks
- medium: any AT RISK deadline OR 1-2 directly blocked tasks
- low: no deadline risks and 0-1 indirectly affected tasks

Return ONLY the JSON. No explanation. No markdown.\
"""

SAFE_DEFAULTS = {
    "directly_blocked": [],
    "indirectly_affected": [],
    "deadline_risks": [],
    "cascade_severity": "low",
    "cascade_summary": "Unable to analyze cascade impact.",
}


def _build_schedule_string(events: list) -> str:
    """Format a list of calendar events into a readable schedule string."""
    if not events:
        return "(No upcoming events)"

    lines = []
    for event in events:
        start_raw = event.get("start", "")
        summary = event.get("summary", "(No title)")
        end_raw = event.get("end", "")

        # Calculate duration
        duration = "?"
        try:
            import re
            strip_tz = lambda s: re.sub(r"[+-]\d{2}:\d{2}$", "", s.strip())
            start_dt = datetime.fromisoformat(strip_tz(start_raw))
            end_dt = datetime.fromisoformat(strip_tz(end_raw))
            duration = str(int((end_dt - start_dt).total_seconds() / 60))
        except Exception:
            pass

        lines.append(f"{start_raw}: {summary} ({duration} min)")

    return "\n".join(lines)


def _parse_llm_json(content: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def resilience_agent(state: PlanBState) -> PlanBState:
    """Resilience Agent — maps the full downstream cascade impact of a disruption.

    This is PlanB's core differentiator. Given a disruption classified by the
    Context Agent, this agent fetches the next 3 days of events and asks Groq
    llama-3.3-70b-versatile to identify which tasks are directly blocked, which
    are indirectly affected, and which deadlines are at risk.

    Reads from state:
        disruption_type (str):  Category of disruption.
        severity (str):         low / medium / high.
        hours_impacted (float): Productive hours lost today.
        context_summary (str):  One-sentence disruption description.

    Writes to state:
        cascade_map (dict):        {directly_blocked, indirectly_affected, cascade_summary}.
        deadline_risks (list):     [{task, deadline, status, reason, recovery}].
        cascade_severity (str):    low / medium / high.
    """
    try:
        disruption_type = state.get("disruption_type") or "work"
        severity = state.get("severity") or "low"
        hours_impacted = state.get("hours_impacted") or 0.0
        context_summary = state.get("context_summary") or state.get("disruption_raw") or "Unknown disruption."

        events = get_events_range(3, phone=state.get("user_phone"))
        schedule_string = _build_schedule_string(events)

        llm = ChatGroq(model=GROQ_MODEL_LARGE, api_key=GROQ_API_KEY)
        prompt = CASCADE_PROMPT.format(
            disruption_type=disruption_type,
            severity=severity,
            hours_impacted=hours_impacted,
            context_summary=context_summary,
            schedule_string=schedule_string,
        )

        response = llm.invoke(prompt)

        try:
            result = _parse_llm_json(response.content)
        except (json.JSONDecodeError, IndexError) as e:
            print(f"Resilience Agent: failed to parse Groq JSON, using defaults: {e}")
            result = SAFE_DEFAULTS

    except Exception as e:
        print(f"Resilience Agent: Groq call failed, using defaults: {e}")
        result = SAFE_DEFAULTS

    state["cascade_map"] = {
        "directly_blocked": result.get("directly_blocked", []),
        "indirectly_affected": result.get("indirectly_affected", []),
        "cascade_summary": result.get("cascade_summary", SAFE_DEFAULTS["cascade_summary"]),
    }
    state["deadline_risks"] = result.get("deadline_risks", [])
    state["cascade_severity"] = result.get("cascade_severity", "low")

    return state
