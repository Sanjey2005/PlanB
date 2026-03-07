"""
Shared scheduling validation rules for PlanB.

Functions moved here from agents/replan/__init__.py so they can be imported
by both the Replan Agent and the Comms Agent without creating circular imports.
"""

import re
from datetime import datetime

_MEAL_WINDOWS = {
    "breakfast": (6,  10),
    "lunch":     (11, 14),
    "dinner":    (18, 21),
    "supper":    (18, 21),
}
_GYM_KEYWORDS   = ("gym", "workout", "exercise", "yoga", "run", "jog", "swim", "lift", "training", "crossfit")
_SLEEP_KEYWORDS = ("sleep", "nap", "rest", "bed")
_MEAL_GENERIC   = ("food", "eat", "meal", "snack")


def _classify_task(task_name: str) -> str | None:
    """Return a category string for hard-rule checking, or None if unconstrained."""
    n = task_name.lower()
    for kw in _MEAL_WINDOWS:
        if kw in n:
            return kw
    for kw in _MEAL_GENERIC:
        if kw in n:
            return "meal_generic"
    for kw in _GYM_KEYWORDS:
        if kw in n:
            return "gym"
    for kw in _SLEEP_KEYWORDS:
        if kw in n:
            return "sleep"
    return None


def _parse_hour(suggested_time: str) -> int | None:
    """Extract the hour (0-23) from an ISO string, clock string, or descriptive phrase."""
    if not suggested_time:
        return None

    # ISO datetime
    try:
        return datetime.fromisoformat(suggested_time).hour
    except (ValueError, TypeError):
        pass

    text = suggested_time.strip()

    # "8:30 AM", "14:00", "8AM", "8 am"
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        return hour

    m = re.search(r"(\d{1,2})\s*(am|pm)", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        ampm = m.group(2).lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        return hour

    # Descriptive keywords → representative hour
    tl = text.lower()
    if "early morning" in tl:   return 6
    if "morning"       in tl:   return 8
    if "noon"          in tl or "midday" in tl: return 12
    if "early afternoon" in tl: return 13
    if "late afternoon" in tl:  return 16
    if "afternoon"     in tl:   return 14
    if "early evening" in tl:   return 17
    if "evening"       in tl:   return 18
    if "night"         in tl:   return 21

    return None


def validate_schedule_item(item: dict) -> dict:
    """Post-LLM validation: reject illogical time moves and override to 'keep'.

    Checks the suggested_time against hard windows for meals, gym, and sleep.
    Also enforces the 3-hour drift limit for same-day moves.
    Returns a (possibly modified) copy of item.
    """
    if item.get("action") != "move":
        return item

    task_name      = item.get("task_name", "")
    suggested_time = item.get("suggested_time", "")
    old_time       = item.get("old_time", "")

    category = _classify_task(task_name)
    if category is None:
        # No hard rules for this task; still check 3-hour drift on same-day moves
        if "tomorrow" not in suggested_time.lower():
            old_hour  = _parse_hour(old_time)
            sugg_hour = _parse_hour(suggested_time)
            if old_hour is not None and sugg_hour is not None:
                if abs(sugg_hour - old_hour) > 3:
                    item = dict(item)
                    item["action"] = "keep"
                    item["suggested_time"] = old_time
                    item["reason"] = (
                        f"Suggested move for '{task_name}' is more than 3 hours from original "
                        f"({old_time} → {suggested_time}). Kept at original time to avoid disruption."
                    )
        return item

    sugg_hour = _parse_hour(suggested_time)
    if sugg_hour is None:
        # Can't parse — trust the LLM if it used a plain word like "tomorrow"
        return item

    violation = False
    reason    = ""

    if category in _MEAL_WINDOWS:
        lo, hi = _MEAL_WINDOWS[category]
        if not (lo <= sugg_hour < hi):
            violation = True
            meal_name = category.capitalize()
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"{meal_name} must be between {lo % 12 or lo}{'AM' if lo < 12 else 'PM'} "
                f"and {hi % 12 or hi}{'AM' if hi < 12 else 'PM'}. Kept at original time."
            )

    elif category == "meal_generic":
        if not (6 <= sugg_hour < 21):
            violation = True
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"meals must stay between 6 AM and 9 PM. Kept at original time."
            )

    elif category == "gym":
        if not (6 <= sugg_hour < 22):
            violation = True
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"gym/workout must be between 6 AM and 10 PM. Kept at original time."
            )

    elif category == "sleep":
        if 9 <= sugg_hour < 21:
            violation = True
            reason = (
                f"Cannot reschedule '{task_name}' to {suggested_time} — "
                f"sleep/rest cannot be moved to daytime hours. Kept at original time."
            )

    if violation:
        item = dict(item)
        item["action"]         = "keep"
        item["suggested_time"] = old_time
        item["reason"]         = reason
        print(f"[SchedulingRules] Validation override: {reason}")

    return item
