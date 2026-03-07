"""
Lifestyle Agent — PlanB

Handles non-scheduling quality-of-life requests: food ordering and cab booking
when the user is working late, hungry, or needs a ride home.

No external API calls are made. All output is deep links and search URLs that
the user taps directly from WhatsApp.
"""

from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from dotenv import load_dotenv

from state import PlanBState

load_dotenv()

# Re-import keyword lists to detect sub-intent from the raw message
from agents.monitor import LATE_OFFICE_KEYWORDS, HUNGRY_KEYWORDS, CAB_KEYWORDS

IST = timezone(timedelta(hours=5, minutes=30))

# Evening routine keywords — events worth suggesting to reschedule when staying late
_EVENING_ROUTINE_KW = ["gym", "workout", "exercise", "yoga", "run", "walk", "dinner"]


# ── Deep link builders ─────────────────────────────────────────────────────────

def _swiggy_url(query: str) -> str:
    return f"https://www.swiggy.com/search?query={quote(query)}"


def _zomato_url(query: str) -> str:
    return f"https://www.zomato.com/search?q={quote(query)}"


def _uber_url(home_address: str = "") -> str:
    base = "https://m.uber.com/ul/?action=setPickup"
    if home_address:
        return f"{base}&dropoff[formatted_address]={quote(home_address)}&dropoff[nickname]=Home"
    return base


def _ola_url() -> str:
    return "https://www.olacabs.com/"


# ── Food suggestions by time of day ───────────────────────────────────────────

def _food_queries() -> list:
    """Return top 3 food search terms appropriate for the current time of day."""
    hour = datetime.now(tz=IST).hour
    if hour < 11:
        return ["poha", "idli sambar", "upma"]
    if hour < 15:
        return ["biryani", "thali", "dal rice"]
    if hour < 18:
        return ["sandwich", "samosa", "chai snacks"]
    return ["biryani", "pizza", "paneer butter masala"]


# ── Evening event detector ─────────────────────────────────────────────────────

def _evening_events_to_reschedule(phone: str = None) -> list:
    """Return summaries of future evening routine/personal events for today."""
    try:
        from utils.google_calendar import get_todays_events
        now = datetime.now(tz=IST)
        events = get_todays_events(phone=phone)
        suggestions = []
        for event in events:
            start_str = event.get("start", "")
            try:
                dt = datetime.fromisoformat(start_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=IST)
                summary = event.get("summary", "")
                if dt > now and any(kw in summary.lower() for kw in _EVENING_ROUTINE_KW):
                    suggestions.append(summary)
            except Exception:
                continue
        return suggestions
    except Exception as e:
        print(f"[Lifestyle] Calendar check failed: {e}")
        return []


# ── Main agent ─────────────────────────────────────────────────────────────────

def lifestyle_agent(state: PlanBState) -> PlanBState:
    """Lifestyle Agent — food and cab suggestions for users working late.

    Reads the original WhatsApp message from state["disruption_raw"] and
    re-checks lifestyle keywords to determine the precise sub-intent, which
    may be food, cab, or a late-office combo (both food and cab).

    For late_office triggers, also checks today's calendar for evening routine
    events (gym, dinner, etc.) and generates a reschedule suggestion.

    Reads from state:
        disruption_raw (str): Original WhatsApp message from the user.
        user_dna (dict):      Used for home_address if set.

    Writes to state:
        lifestyle_actions (list): Ordered list of action dicts, each with a
                                  "type" key and relevant links/suggestions.
    """
    try:
        message = (state.get("disruption_raw") or "").lower()
        user_dna = state.get("user_dna") or {}
        home_address = user_dna.get("home_address", "")

        is_late = any(kw in message for kw in LATE_OFFICE_KEYWORDS)
        is_food = any(kw in message for kw in HUNGRY_KEYWORDS)
        is_cab = any(kw in message for kw in CAB_KEYWORDS)

        # "Staying late" implicitly means: suggest food AND a cab home
        if is_late and not is_food and not is_cab:
            is_food = True
            is_cab = True

        lifestyle_actions = []

        # ── Food action ───────────────────────────────────────────────────────
        if is_food:
            queries = _food_queries()
            lifestyle_actions.append({
                "type": "food",
                "queries": queries,
                "links": [
                    {
                        "name": q.title(),
                        "swiggy": _swiggy_url(q),
                        "zomato": _zomato_url(q),
                    }
                    for q in queries
                ],
            })

        # ── Cab action ────────────────────────────────────────────────────────
        if is_cab:
            lifestyle_actions.append({
                "type": "cab",
                "uber_url": _uber_url(home_address),
                "ola_url": _ola_url(),
            })

        # ── Late-office: suggest rescheduling evening events ──────────────────
        if is_late:
            events_to_move = _evening_events_to_reschedule(phone=state.get("user_phone"))
            if events_to_move:
                lifestyle_actions.append({
                    "type": "reschedule_suggestion",
                    "events": events_to_move,
                })

        state["lifestyle_actions"] = lifestyle_actions

    except Exception as e:
        print(f"[Lifestyle] Unexpected error: {e}")
        state["lifestyle_actions"] = []

    return state
