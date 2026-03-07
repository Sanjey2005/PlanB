from datetime import datetime

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_FAST, GROQ_API_KEY
from state import PlanBState
from utils.google_calendar import get_todays_events, get_events_range
from utils.habit_learner import get_learned_scores
from utils.llm_utils import parse_llm_json

load_dotenv()

# Importance weights by task type
IMPORTANCE_MAP = {
    "work_deliverable": 1.0,
    "meeting": 0.7,
    "routine": 0.5,
    "personal": 0.3,
}

# Energy cost keyword groups (checked against lowercased event summary)
ENERGY_HIGH_KW = ["deep work", "coding", "writing", "analysis", "design", "research", "proposal"]
ENERGY_MED_KW = ["meeting", "call", "sync", "review", "discussion"]
ENERGY_LOW_KW = ["gym", "workout", "exercise", "walk", "run"]
ENERGY_MIN_KW = ["reading", "email", "admin", "planning"]

# Fatigue multipliers
FATIGUE_MAP = {
    "none": 0.0,
    "low": 0.2,
    "medium": 0.5,
    "high": 0.9,
}

# In-memory cache: event summary -> task_type string
_classification_cache: dict = {}


def _batch_classify_events(events: list, llm, user_phone: str = "") -> dict:
    """Return a dict mapping event summary -> task_type for all unclassified events.

    Only events without planb_task_type in extendedProperties are sent to Groq.
    Results are read from and written back to _classification_cache, keyed by
    (user_phone, summary) to prevent cross-user leakage.
    """
    to_classify = []
    for event in events:
        summary = event.get("summary", "Untitled event")
        extended = event.get("extendedProperties", {})
        private = extended.get("private", {}) if isinstance(extended, dict) else {}
        task_type = private.get("planb_task_type", "").strip().lower()
        if task_type in IMPORTANCE_MAP:
            continue  # already typed via extendedProperties
        if (user_phone, summary) not in _classification_cache:
            to_classify.append(summary)

    if to_classify:
        numbered = "\n".join(f'{i+1}. "{s}"' for i, s in enumerate(to_classify))
        prompt = (
            "Classify each calendar event into exactly one category.\n"
            "Categories: work_deliverable, meeting, routine, personal\n\n"
            f"{numbered}\n\n"
            "Reply with ONLY a JSON object mapping each event name to its category. "
            'Example: {"Team standup": "meeting", "Gym": "routine"}'
        )
        try:
            response = llm.invoke(prompt)
            result = parse_llm_json(response.content)
            for summary, task_type in result.items():
                t = task_type.strip().lower()
                if t in IMPORTANCE_MAP:
                    _classification_cache[(user_phone, summary)] = t
        except Exception as e:
            print(f"Priority Engine: batch classification failed: {e}")
            for summary in to_classify:
                if (user_phone, summary) not in _classification_cache:
                    _classification_cache[(user_phone, summary)] = ""

    # Build final dict covering every event
    classifications = {}
    for event in events:
        summary = event.get("summary", "Untitled event")
        extended = event.get("extendedProperties", {})
        private = extended.get("private", {}) if isinstance(extended, dict) else {}
        task_type = private.get("planb_task_type", "").strip().lower()
        if task_type in IMPORTANCE_MAP:
            classifications[summary] = task_type
        else:
            classifications[summary] = _classification_cache.get((user_phone, summary), "")
    return classifications


def _get_deadline_proximity(event: dict, current_hour: int = 0) -> float:
    """Return deadline_proximity score (0-1) based on how soon the event starts.

    For same-day events, uses hour-level precision so past events score 0.
    """
    from datetime import datetime
    import re

    start_raw = event.get("start", "")
    if not start_raw:
        return 0.1

    try:
        # Normalise ISO 8601 offset (+05:30 → strip to naive for simple comparison)
        start_str = re.sub(r"[+-]\d{2}:\d{2}$", "", start_raw)
        event_dt = datetime.fromisoformat(start_str)
        event_date = event_dt.date()
        today = datetime.now().date()
        delta = (event_date - today).days

        if delta < 0:
            # Event was on a past day
            return 0.0
        elif delta == 0:
            # Same day — use hour precision
            event_hour = event_dt.hour
            if event_hour < current_hour:
                return 0.0  # already past
            elif event_hour - current_hour <= 2:
                return 1.0  # imminent
            elif event_hour - current_hour <= 6:
                return 0.7  # later today
            else:
                return 0.5  # end of day
        elif delta == 1:
            return 0.75
        elif delta <= 7:
            return 0.5
        else:
            return 0.1
    except Exception:
        return 0.1


def _get_importance(event: dict, classifications: dict) -> float:
    """Return importance score (0-1) using pre-built classifications dict."""
    summary = event.get("summary", "Untitled event")
    task_type = classifications.get(summary, "")
    return IMPORTANCE_MAP.get(task_type, 0.5)


def _get_energy_cost(event: dict) -> float:
    """Return energy cost (0-1) based on keywords in the event summary."""
    summary = event.get("summary", "").lower()

    if any(kw in summary for kw in ENERGY_HIGH_KW):
        return 1.0
    if any(kw in summary for kw in ENERGY_MED_KW):
        return 0.6
    if any(kw in summary for kw in ENERGY_LOW_KW):
        return 0.4
    if any(kw in summary for kw in ENERGY_MIN_KW):
        return 0.3
    return 0.5


def _score_event(event: dict, fatigue_multiplier: float, classifications: dict, current_hour: int = 0) -> int:
    """Compute and return a clamped 0-100 priority score for a single event."""
    deadline_proximity = _get_deadline_proximity(event, current_hour=current_hour)
    urgency = deadline_proximity                     # reinforce each other
    importance = _get_importance(event, classifications)
    goal_alignment = importance                      # reinforce each other
    energy_cost = _get_energy_cost(event)

    raw_score = (
        (urgency * deadline_proximity)
        + (importance * goal_alignment)
        - (energy_cost * fatigue_multiplier)
    )

    # Scale to 0-100 and clamp
    scaled = raw_score * 50          # max raw ≈ 2.0 → maps to 100
    return max(0, min(100, round(scaled)))


def priority_engine(state: PlanBState) -> PlanBState:
    """Priority Engine Agent — scores every upcoming task on a 0-100 scale.

    Formula:
        priority_score = (urgency * deadline_proximity)
                       + (importance * goal_alignment)
                       - (energy_cost * fatigue_multiplier)

    Events are fetched from Google Calendar (today + next 2 days), deduplicated
    by event id, then each scored and written to state["task_scores"] as a
    {event_id: score} dict.

    Reads from state:
        fatigue_level (str): Fatigue level set by the Context Agent.

    Writes to state:
        task_scores (dict): {event_id: int} priority scores, 0-100.
    """
    try:
        fatigue_level = state.get("fatigue_level") or "none"
        fatigue_multiplier = FATIGUE_MAP.get(fatigue_level, 0.0)
        current_hour = state.get("current_hour") or datetime.now().hour

        llm = ChatGroq(model=GROQ_MODEL_FAST, api_key=GROQ_API_KEY)

        # Fetch and deduplicate events
        user_phone = state.get("user_phone")
        today_events = get_todays_events(phone=user_phone)
        range_events = get_events_range(2, phone=user_phone)

        seen_ids = set()
        all_events = []
        for event in today_events + range_events:
            eid = event.get("id")
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                all_events.append(event)

        # Single batch Groq call for all unclassified events
        classifications = _batch_classify_events(all_events, llm, user_phone=state.get("user_phone") or "")

        task_scores = {}
        for event in all_events:
            eid = event.get("id")
            if not eid:
                continue
            try:
                score = _score_event(event, fatigue_multiplier, classifications, current_hour=current_hour)
                task_scores[eid] = score
            except Exception as e:
                print(f"Priority Engine: failed to score event '{event.get('summary')}': {e}")
                task_scores[eid] = 0

        # Apply habit learning adjustments based on user override history
        try:
            summary_to_id = {e.get("summary", ""): e.get("id") for e in all_events if e.get("id")}
            all_summaries = list(summary_to_id.keys())
            learned = get_learned_scores(all_summaries, user_phone=state.get("user_phone") or "")
            for summary, adj in learned.items():
                if adj <= 0:
                    continue
                eid = summary_to_id.get(summary)
                if eid and eid in task_scores:
                    task_scores[eid] = min(100, task_scores[eid] + adj)
                    print(f"[Habit Learning] Adjusted {summary} by +{adj}")
        except Exception as e:
            print(f"Priority Engine: habit learning adjustment failed: {e}")

        # Apply User DNA overrides — protected habits and never-reschedule rules
        try:
            user_dna = state.get("user_dna") or {}
            protected_habits = user_dna.get("protected_habits") or []
            never_reschedule = user_dna.get("never_reschedule") or []

            if protected_habits or never_reschedule:
                for event in all_events:
                    eid = event.get("id")
                    if not eid:
                        continue
                    summary = event.get("summary", "")
                    summary_lower = summary.lower()

                    if any(nr.lower() in summary_lower for nr in never_reschedule):
                        task_scores[eid] = 100
                        print(f"[UserDNA] Never-reschedule: '{summary}' pinned to 100")
                    elif any(ph.lower() in summary_lower for ph in protected_habits):
                        task_scores[eid] = min(100, task_scores.get(eid, 0) + 30)
                        print(f"[UserDNA] Protected habit boost: '{summary}' +30")
        except Exception as e:
            print(f"Priority Engine: User DNA adjustment failed: {e}")

        state["task_scores"] = task_scores
        return state

    except Exception as e:
        print(f"Priority Engine: unexpected error: {e}")
        state["task_scores"] = {}
        return state
