"""
Predictive Risk Agent — PlanB Scheduling Assistant

Proactively scans the upcoming 7 days of Google Calendar events to identify scheduling
risks BEFORE they become disruptions. Runs only during morning_briefing and evening_review
modes.

Risk categories detected:
  1. Burnout — 3+ deep-work/focus blocks back-to-back without a buffer.
  2. Deadline compression — tasks with deadlines that lack sufficient prep time.
  3. Energy misalignment — heavy cognitive tasks scheduled after 8 PM IST.
  4. Overload — days with more than 8 hours of scheduled activity.
  5. Missing buffers — back-to-back meetings with no transition time.

For HIGH severity risks flagged as auto-fixable, the agent automatically inserts a
30-minute "PlanB: Recovery Buffer" block into Google Calendar on the affected date.

LangGraph node — reads from and writes to PlanBState only.
"""

from dotenv import load_dotenv

load_dotenv()

import json
import re
from collections import defaultdict
from datetime import datetime

from langchain_groq import ChatGroq

from state import PlanBState
from config.settings import GROQ_MODEL_LARGE, GROQ_API_KEY
from utils.google_calendar import get_events_range, create_event, get_free_slots


def _build_week_summary(events: list) -> str:
    """Group events by date and format as a readable text block."""
    by_date = defaultdict(list)

    for ev in events:
        start_str = ev.get("start", "")
        summary = ev.get("summary", "(No title)")

        if not start_str:
            continue

        try:
            start_dt = datetime.fromisoformat(start_str)
        except (ValueError, TypeError):
            continue

        date_key = start_dt.strftime("%Y-%m-%d (%A)")
        time_str = start_dt.strftime("%I:%M %p")

        end_str = ev.get("end", "")
        duration = ""
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str)
                mins = int((end_dt - start_dt).total_seconds() / 60)
                if mins >= 60:
                    duration = f"{mins // 60}h {mins % 60}m" if mins % 60 else f"{mins // 60}h"
                else:
                    duration = f"{mins}m"
            except (ValueError, TypeError):
                pass

        entry = f"  - {time_str} | {summary}"
        if duration:
            entry += f" ({duration})"
        by_date[date_key].append(entry)

    if not by_date:
        return ""

    lines = []
    for date_key in sorted(by_date.keys()):
        lines.append(date_key)
        lines.extend(by_date[date_key])
        lines.append("")

    return "\n".join(lines)


def _parse_risks_json(text: str) -> list:
    """Strip markdown fences and parse JSON safely."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    return []


def _auto_fix_buffer(risk: dict) -> None:
    """Insert a 30-minute recovery buffer on the affected date for a high-severity risk."""
    date_str = risk.get("date", "")
    if not date_str:
        return

    try:
        # Try to find a free slot on that date
        free_slots = get_free_slots(date_str, 30)

        if free_slots:
            slot = free_slots[0]
            start = slot["start"]
            end = slot["end"]
        else:
            # Default to 12:30 PM IST
            start = f"{date_str}T12:30:00+05:30"
            end = f"{date_str}T13:00:00+05:30"

        create_event(
            summary="PlanB: Recovery Buffer",
            start=start,
            end=end,
            metadata={
                "planb_task_type": "routine",
                "planb_priority_score": "30",
            },
        )
        print(f"[Predictive Risk] Inserted buffer block on {date_str}")

    except Exception as e:
        print(f"[Predictive Risk] Auto-fix failed for {date_str}: {e}")


def predictive_risk_agent(state: PlanBState) -> PlanBState:
    """Predictive Risk Agent — scans the week ahead for scheduling risks.

    Only runs during morning_briefing and evening_review modes. Fetches the next
    7 days of calendar events, analyses them with Groq LLM for five risk categories
    (burnout, deadline compression, energy misalignment, overload, missing buffers),
    and auto-fixes high-severity auto-fixable risks by inserting recovery buffer blocks.

    Reads:
        state['mode'] — must be 'morning_briefing' or 'evening_review' to proceed.

    Writes:
        state['predictive_risks'] — list of risk dicts with keys: type, severity,
                                     date, detail, intervention, auto_fix.

    Returns:
        Updated PlanBState.
    """
    try:
        mode = state.get("mode")
        if mode not in ("morning_briefing", "evening_review"):
            return state

        # Fetch next 7 days of events
        events = get_events_range(7)
        if not events:
            state["predictive_risks"] = []
            return state

        # Build week summary
        week_summary = _build_week_summary(events)
        if not week_summary:
            state["predictive_risks"] = []
            return state

        # Call Groq LLM for risk analysis
        llm = ChatGroq(
            model_name=GROQ_MODEL_LARGE,
            api_key=GROQ_API_KEY,
            temperature=0.3,
        )

        prompt = (
            "You are a proactive AI scheduling assistant analysing the week ahead for risks.\n\n"
            f"Schedule for the next 7 days:\n{week_summary}\n\n"
            "Identify risks in these categories:\n"
            "1. Burnout risk: 3 or more deep work / focus blocks back-to-back with no buffer\n"
            "2. Deadline compression: tasks with deadlines that have insufficient prep time in the schedule\n"
            "3. Energy misalignment: heavy cognitive tasks scheduled after 8pm IST\n"
            "4. Overload days: days with more than 8 hours of scheduled activity\n"
            "5. Missing buffers: back-to-back meetings with no transition time\n\n"
            "Return ONLY valid JSON as a list of risks:\n"
            "[\n"
            "  {\n"
            '    "type": "one of [burnout, deadline_compression, energy_misalignment, overload, missing_buffer]",\n'
            '    "severity": "one of [low, medium, high]",\n'
            '    "date": "string (YYYY-MM-DD of the affected day)",\n'
            '    "detail": "string (specific description of the risk)",\n'
            '    "intervention": "string (specific action to fix it)",\n'
            '    "auto_fix": "bool (true if PlanB can fix this automatically by inserting a buffer block)"\n'
            "  }\n"
            "]\n\n"
            "If no risks found, return an empty list [].\n"
            "Return ONLY the JSON. No explanation. No markdown."
        )

        response = llm.invoke(prompt)
        risks = _parse_risks_json(response.content)

        # Auto-fix high severity risks where auto_fix is true
        for risk in risks:
            try:
                is_high = str(risk.get("severity", "")).lower() == "high"
                can_auto_fix = risk.get("auto_fix") is True or str(risk.get("auto_fix", "")).lower() == "true"

                if is_high and can_auto_fix:
                    _auto_fix_buffer(risk)
            except Exception as e:
                print(f"[Predictive Risk] Error during auto-fix: {e}")
                continue

        state["predictive_risks"] = risks
        print(f"[Predictive Risk] Identified {len(risks)} risk(s) for the week ahead")

    except Exception as e:
        print(f"[Predictive Risk] Agent error: {e}")

    return state
