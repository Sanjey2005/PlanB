from typing import TypedDict, Optional
from dotenv import load_dotenv

load_dotenv()


class PlanBState(TypedDict, total=False):
    """Shared state for all PlanB agents.

    Every agent reads from and writes to this single TypedDict.
    Data flows through the LangGraph pipeline via this state — agents
    never pass data to each other directly.
    """

    # Monitor Agent
    disruption_raw: Optional[str]
    disruption_source: Optional[str]  # 'gmail_webhook', 'calendar_change', 'user_message', 'scheduled'
    mode: Optional[str]  # 'disruption', 'morning_briefing', 'on_demand', 'evening_review', 'query', 'onboarding'
    is_new_user: Optional[bool]

    # Context Agent
    disruption_type: Optional[str]
    severity: Optional[str]  # 'low', 'medium', 'high'
    hours_impacted: Optional[float]
    fatigue_level: Optional[str]  # 'none', 'low', 'medium', 'high'
    context_summary: Optional[str]

    # Resilience Agent
    cascade_map: Optional[dict]
    deadline_risks: Optional[list]
    cascade_severity: Optional[str]

    # Priority Engine
    task_scores: Optional[dict]

    # Orchestrator
    agents_to_fire: Optional[list]
    delegation_depth: Optional[str]  # 'advisory', 'assisted', 'autonomous'
    schedule_conflict: Optional[bool]

    # Replan Agent
    proposed_schedule: Optional[list]

    # Routine Agent
    routine_decisions: Optional[dict]

    # Scheduler Agent
    confirmed_schedule: Optional[list]
    moved_meetings: Optional[list]
    confidence_scores: Optional[dict]

    # Negotiate Agent
    emails_sent: Optional[list]

    # Predictive Risk Agent
    predictive_risks: Optional[list]

    # Crisis Agent
    crisis_mode: Optional[bool]
    crisis_actions: Optional[list]
    decision_reasoning: Optional[str]  # Human-readable explanation of orchestrator routing decision

    # Stress Agent
    stress_mode: Optional[bool]
    stress_actions: Optional[list]

    # Undo Agent
    undo_result: Optional[dict]

    # Lifestyle Agent
    lifestyle_actions: Optional[list]

    # User DNA profile (loaded at pipeline start, persisted at end)
    user_dna: Optional[dict]

    # Onboarding OAuth
    oauth_url: Optional[str]

    # Time-awareness (injected at pipeline start)
    current_time: Optional[str]   # ISO datetime at pipeline start e.g. "2026-03-07T18:00:00+05:30"
    current_hour: Optional[int]   # 0-23 hour in IST

    # Final output
    whatsapp_message: Optional[str]
    pipeline_complete: Optional[bool]
    user_phone: Optional[str]


def get_initial_state() -> dict:
    """Return a dict with all PlanBState fields set to None."""
    return {
        "disruption_raw": None,
        "disruption_source": None,
        "mode": None,
        "is_new_user": None,
        "disruption_type": None,
        "severity": None,
        "hours_impacted": None,
        "fatigue_level": None,
        "context_summary": None,
        "cascade_map": None,
        "deadline_risks": None,
        "cascade_severity": None,
        "task_scores": None,
        "agents_to_fire": None,
        "delegation_depth": None,
        "schedule_conflict": None,
        "proposed_schedule": None,
        "routine_decisions": None,
        "confirmed_schedule": None,
        "moved_meetings": None,
        "confidence_scores": None,
        "emails_sent": None,
        "predictive_risks": None,
        "crisis_mode": None,
        "crisis_actions": None,
        "decision_reasoning": None,
        "stress_mode": None,
        "stress_actions": None,
        "undo_result": None,
        "lifestyle_actions": None,
        "user_dna": None,
        "oauth_url": None,
        "current_time": None,
        "current_hour": None,
        "whatsapp_message": None,
        "pipeline_complete": None,
        "user_phone": None,
    }
