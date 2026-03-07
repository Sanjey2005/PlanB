from datetime import datetime, timedelta
from urllib.parse import quote

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_FAST, GROQ_API_KEY
from state import PlanBState
from utils.google_calendar import get_todays_events, create_event, get_free_slots
from utils.whatsapp import send_message

load_dotenv()

POLISH_PROMPT = """\
Clean up this WhatsApp message. Rules:
- Keep it under 300 words
- Plain text only, no markdown, no asterisks, no headers
- Use bullet points with • character only
- Keep all the facts exactly as given, do not change numbers or times
- Keep emoji only where already present (✓ ⚠)
- Sound helpful and human, not robotic
- For morning and evening messages: keep tone warm and conversational. Max 200 words. Never start with 'Here is' or 'Below is'. Sound like a smart friend, not a calendar app.
- If any schedule change looks unrealistic (lunch at 8 AM, gym at 4 AM, dinner at noon), describe that task as kept at its original time — do not mention or explain the illogical change.
- Tone: warm, practical, direct. Never sarcastic. Never make jokes about inconvenient schedule changes. Treat the user's time as something important. A professional assistant would never say things like "hope you're a morning person now".
- NEVER add, invent, or modify event names, times, or schedule items. The events listed in this message are exactly what is on the user's calendar. Do not add, remove, or change any of them.
Message: {raw_message}\
"""

_PERSONAL_EVENT_KEYWORDS = (
    "date", "girlfriend", "boyfriend", "plans with", "invited me",
    "going out", "family dinner", "unexpected plans", "something came up",
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _day_of_week_closer() -> str:
    day = datetime.now().weekday()  # 0=Monday, 6=Sunday
    if day == 0:
        return "New week. Let's make it count."
    if day == 4:
        return "Final push. You've got this."
    return "Your day is set. Focus on what matters."


def _scored_events_today(state: PlanBState, n: int = 3) -> list:
    """Return top-n (name, score) tuples from today's calendar, sorted by score desc."""
    try:
        from utils.google_calendar import get_todays_events
        events = get_todays_events(phone=state.get("user_phone"))
        task_scores = state.get("task_scores") or {}
        scored = []
        for e in events:
            eid = e.get("id")
            name = e.get("summary", "(No title)")
            score = task_scores.get(eid)
            if score is not None:
                scored.append((name, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]
    except Exception:
        return []


def _tomorrows_top_events(state: PlanBState, n: int = 3) -> list:
    """Return top-n (name, score, time_str) tuples for tomorrow, sorted by score desc."""
    try:
        from utils.google_calendar import get_events_range
        events = get_events_range(2, phone=state.get("user_phone"))
        tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        task_scores = state.get("task_scores") or {}
        result = []
        for e in events:
            start_str = e.get("start", "")
            try:
                dt = datetime.fromisoformat(start_str)
            except (ValueError, TypeError):
                continue
            if dt.strftime("%Y-%m-%d") != tomorrow_str:
                continue
            name = e.get("summary", "(No title)")
            eid = e.get("id")
            score = task_scores.get(eid, 0)
            h = dt.hour % 12 or 12
            ampm = "am" if dt.hour < 12 else "pm"
            time_str = f"{h}:{dt.minute:02d}{ampm}"
            result.append((name, score, time_str))
        result.sort(key=lambda x: x[1], reverse=True)
        return result[:n]
    except Exception:
        return []


def _format_today_risk(risk: dict, scored_desc: list) -> str:
    """Format a single predictive risk as a plain-text warning line."""
    rtype = risk.get("type", "")
    detail = risk.get("detail") or risk.get("description") or ""
    if rtype == "burnout":
        return f"\u26a0 Burnout risk: {detail}"
    if rtype == "deadline_compression":
        return f"\u26a0 Deadline pressure: {detail}"
    if rtype == "energy_misalignment":
        return f"\u26a0 Energy mismatch: {detail}"
    if rtype == "missing_buffer":
        return f"\u26a0 No buffer time: {detail}"
    if rtype == "overload":
        low_task = scored_desc[-1][0] if scored_desc else ""
        suffix = f" Consider dropping {low_task}." if low_task else ""
        return f"\u26a0 Overloaded day: {detail}{suffix}"
    return f"\u26a0 Risk: {detail}"


# ── Message builders ───────────────────────────────────────────────────────────

def _build_disruption_message(state: PlanBState) -> str:
    lines = []

    # What the disruption was
    disruption_summary = state.get("context_summary") or state.get("disruption_raw") or "A disruption occurred."
    raw_lower = (state.get("disruption_raw") or "").lower()
    if any(kw in raw_lower for kw in _PERSONAL_EVENT_KEYWORDS):
        lines.append("Got it! I've blocked time for your personal plans and rearranged the rest of your day.")
    else:
        lines.append(disruption_summary)
    lines.append("")

    # What changed
    confirmed = state.get("confirmed_schedule") or []
    moved = [t for t in confirmed if t.get("new_time") and t.get("old_time")]
    if moved:
        lines.append("What changed:")
        for t in moved:
            entry = f"• {t['task_name']}: {t['old_time']} → {t['new_time']} (confidence: {t.get('confidence', 0)}%)"
            if t.get("moved_to_tomorrow"):
                entry += " (moved to tomorrow)"
            lines.append(entry)
        lines.append("")

    # What stayed (routines kept)
    routine_decisions = state.get("routine_decisions") or {}
    kept = [name for name, data in routine_decisions.items() if data.get("decision") == "kept"]
    if kept:
        lines.append("What stayed:")
        for name in kept:
            lines.append(f"• {name} ✓")
        lines.append("")

    # Streak protection notices
    streak_protected = [
        name for name, data in routine_decisions.items()
        if data.get("streak_protected") is True
    ]
    if streak_protected:
        for name in streak_protected:
            lines.append(f"Streak protection: {name} kept despite pressure (would have been dropped)")
        lines.append("")

    # Reschedule emails
    emails_sent = state.get("emails_sent") or []
    if emails_sent:
        recipients = ", ".join(emails_sent)
        lines.append(f"Reschedule emails sent to: {recipients}")
        lines.append("")

    # Deadline risks
    deadline_risks = state.get("deadline_risks") or []
    at_risk = [r for r in deadline_risks if r.get("status") in ("AT RISK", "CRITICAL")]
    if at_risk:
        for r in at_risk:
            lines.append(f"⚠ {r.get('task', 'Task')}: {r.get('reason', '')}")
        lines.append("")

    lines.append("Your calendar is updated. Reply 'undo' to revert any change.")
    return "\n".join(lines)


def _build_morning_briefing_message(state: PlanBState) -> str:
    hour = datetime.now().hour
    greeting = "Good morning!" if hour < 9 else "Morning!"
    lines = [greeting, ""]

    # Energy check based on fatigue_level
    fatigue = (state.get("fatigue_level") or "none").lower()
    if fatigue in ("none", "low"):
        lines.append("You're starting fresh today.")
    elif fatigue == "medium":
        lines.append("Pacing yourself today — that's smart.")
    else:
        lines.append("Take it easy today. I've adjusted your load.")
    lines.append("")

    # Your day at a glance — ALL events from real calendar, sorted by start time
    # Never filter by score: scores may be absent for query/briefing paths
    events = get_todays_events(phone=state.get("user_phone"))
    task_scores = state.get("task_scores") or {}
    timed_events = []
    for e in events:
        start_str = e.get("start", "")
        name = e.get("summary", "(No title)")
        try:
            dt = datetime.fromisoformat(start_str)
            h = dt.hour % 12 or 12
            ampm = "AM" if dt.hour < 12 else "PM"
            time_str = f"{h}:{dt.minute:02d} {ampm}"
        except (ValueError, TypeError):
            dt = None
            time_str = start_str
        score = task_scores.get(e.get("id"), 0)
        timed_events.append((dt, name, time_str, score))
    timed_events.sort(key=lambda x: (x[0] is None, x[0]))

    if timed_events:
        lines.append("📅 Here's your day:")
        for _dt, name, time_str, _score in timed_events:
            lines.append(f"• {time_str} — {name}")
        lines.append("")

    # Watch out — single highest-severity risk for today
    today_str = datetime.now().strftime("%Y-%m-%d")
    predictive_risks = state.get("predictive_risks") or []
    today_risks = [
        r for r in predictive_risks
        if r.get("date") == today_str and r.get("severity") in ("high", "medium")
    ]
    if today_risks:
        severity_order = {"high": 0, "medium": 1}
        top_risk = min(today_risks, key=lambda r: severity_order.get(r.get("severity", "medium"), 1))
        detail = top_risk.get("detail") or top_risk.get("description") or ""
        lines.append(f"Watch out: {detail}")
        lines.append("")

    # Focus on — highest-scored event, or first event if no scores present
    if timed_events:
        scored = [(name, score) for _, name, _, score in timed_events if score > 0]
        focus = max(scored, key=lambda x: x[1])[0] if scored else timed_events[0][1]
        lines.append(f"Focus on: {focus}")
        lines.append("")

    lines.append(_day_of_week_closer())
    return "\n".join(lines)


def _build_evening_review_message(state: PlanBState) -> str:
    lines = []

    # Day wrap — routine tasks that stayed on plan
    routine_decisions = state.get("routine_decisions") or {}
    kept = [name for name, data in routine_decisions.items() if data.get("decision") == "kept"]
    lines.append("Day wrap:")
    if kept:
        for name in kept:
            lines.append(f"• {name} \u2713")
    else:
        lines.append("No routine tasks recorded today.")
    lines.append("")

    # What moved — rescheduled confirmed tasks
    confirmed = state.get("confirmed_schedule") or []
    moved = [t for t in confirmed if t.get("old_time") and t.get("new_time")]
    if moved:
        lines.append("What moved:")
        for t in moved:
            reason = t.get("reason") or "rescheduled"
            lines.append(f"• {t['task_name']}: {t['old_time']} \u2192 {t['new_time']} ({reason})")
        lines.append("")

    # Tomorrow — top 3 events from next day
    tomorrow_events = _tomorrows_top_events(state, n=3)
    lines.append("Tomorrow:")
    if tomorrow_events:
        for name, _score, time_str in tomorrow_events:
            lines.append(f"• {time_str} — {name}")
    else:
        lines.append("Nothing scheduled yet.")
    lines.append("")

    # Risk to watch — highest predictive risk for tomorrow
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    predictive_risks = state.get("predictive_risks") or []
    tomorrow_risks = [
        r for r in predictive_risks
        if r.get("date") == tomorrow_str and r.get("severity") in ("high", "medium")
    ]
    if tomorrow_risks:
        severity_order = {"high": 0, "medium": 1}
        top_risk = min(tomorrow_risks, key=lambda r: severity_order.get(r.get("severity", "medium"), 1))
        detail = top_risk.get("detail") or top_risk.get("description") or ""
        lines.append(f"Risk to watch: {detail}")
        if top_risk.get("type") == "missing_buffer":
            lines.append("Reply 'buffer it' to fix this.")
        else:
            intervention = top_risk.get("intervention") or ""
            if intervention:
                lines.append(f"Tip: {intervention}")
        lines.append("")

    lines.append("Rest well.")
    return "\n".join(lines)


def _build_query_message(state: PlanBState) -> str:
    raw = state.get("disruption_raw") or ""

    if raw == "HABIT_STATS_REQUEST":
        # Primary: User DNA profile (richer, cross-run data)
        user_dna = state.get("user_dna") or {}
        if user_dna.get("total_pipeline_runs", 0) > 0:
            lines = ["Your PlanB DNA:"]

            protected = user_dna.get("protected_habits") or []
            if protected:
                lines.append(f"Protected: {', '.join(protected)}")

            peak_hours = user_dna.get("peak_hours") or []
            if peak_hours:
                lines.append(f"Peak hours: {', '.join(peak_hours)}")

            total_runs = user_dna.get("total_pipeline_runs", 0)
            lines.append(f"Total runs: {total_runs}")

            streak_records = user_dna.get("streak_records") or {}
            active_streaks = [
                f"{name} {rec.get('kept_streak', 0)} days"
                for name, rec in streak_records.items()
                if rec.get("kept_streak", 0) > 0
            ]
            if active_streaks:
                lines.append(f"Streaks: {', '.join(active_streaks)}")

            return "\n".join(lines)

        # Fallback: raw habit learner stats (single-run data)
        try:
            from utils.habit_learner import get_all_habit_stats
            stats = get_all_habit_stats(user_phone=state.get("user_phone") or "")
            if not stats:
                return "No habit data found yet. Keep using PlanB and your stats will appear here."

            lines = ["Your habit stats:"]
            for task_name, s in sorted(stats.items()):
                kept = s.get("times_kept", 0)
                total = s.get("total", 0)
                boost = s.get("score_boost", 0)
                line = f"• {task_name}: kept {kept}/{total} days"
                if boost > 0:
                    line += f", score boosted +{boost}"
                lines.append(line)
            return "\n".join(lines)
        except Exception as e:
            print(f"Comms Agent: habit stats fetch failed: {e}")
            return "Could not load habit stats right now. Please try again later."

    # All other queries — always fetch real calendar data, never let LLM invent events
    try:
        events = get_todays_events(phone=state.get("user_phone"))
    except Exception:
        events = []

    if not events:
        return "Nothing scheduled today."

    lines = ["📅 Your day:"]
    for e in events:
        start_str = e.get("start", "")
        name = e.get("summary", "(No title)")
        try:
            dt = datetime.fromisoformat(start_str)
            h = dt.hour % 12 or 12
            ampm = "AM" if dt.hour < 12 else "PM"
            time_str = f"{h}:{dt.minute:02d} {ampm}"
        except (ValueError, TypeError):
            time_str = start_str
        lines.append(f"• {time_str} — {name}")
    return "\n".join(lines)


def _build_stress_message(state: PlanBState) -> str:
    lines = ["Sounds like today is a lot."]
    lines.append("")

    stress_actions = state.get("stress_actions") or []

    # What was lightened
    lightened = [a for a in stress_actions if a.get("action") == "lightened"]
    if lightened:
        lines.append("I've moved a few things off your plate for today:")
        for a in lightened:
            lines.append(f"• {a['task_name']}")
        lines.append("")

    lines.append("I've cleared some space. Focus on just 2 things today.")
    lines.append("")
    lines.append("You've got this.")
    return "\n".join(lines)


def _build_crisis_message(state: PlanBState) -> str:
    lines = ["CRISIS MODE ACTIVATED. PlanB has taken emergency action."]
    lines.append("")

    crisis_actions = state.get("crisis_actions") or []

    # Tasks dropped
    dropped = [a for a in crisis_actions if a.get("action") == "dropped"]
    if dropped:
        lines.append("Dropped low-priority tasks:")
        for a in dropped:
            lines.append(f"• {a['task_name']} (score too low for crisis mode)")
        lines.append("")

    # DND block
    dnd = next((a for a in crisis_actions if a.get("action") == "calendar_block_created"), None)
    if dnd:
        lines.append(f"Calendar block created: {dnd.get('summary', 'DND block')} ({dnd.get('start', '')} to {dnd.get('end', '')})")
        lines.append("")

    # DND emails
    emails = [a for a in crisis_actions if a.get("action") == "dnd_email"]
    sent = [a for a in emails if a.get("status") == "sent"]
    if sent:
        recipients = ", ".join(a["to"] for a in sent)
        lines.append(f"DND notice emails sent to: {recipients}")
        lines.append("")

    lines.append("Focus on what matters. PlanB has handled the rest.")
    return "\n".join(lines)


def _build_undo_message(state: PlanBState) -> str:
    undo_result = state.get("undo_result") or {}
    reverted = undo_result.get("reverted") or []

    if not reverted:
        return "Nothing to undo — no recent changes found."

    lines = ["Done. Here's what I put back:"]
    for item in reverted:
        task_name = item.get("task_name", "Unknown")
        reverted_to = item.get("reverted_to", "original time")
        lines.append(f"• {task_name} \u2192 {reverted_to}")
    lines.append("")
    lines.append("Your calendar is back to how it was.")
    return "\n".join(lines)


def _build_lifestyle_message(state: PlanBState) -> str:
    actions = state.get("lifestyle_actions") or []

    food_action = next((a for a in actions if a.get("type") == "food"), None)
    cab_action = next((a for a in actions if a.get("type") == "cab"), None)
    reschedule_action = next((a for a in actions if a.get("type") == "reschedule_suggestion"), None)

    lines = []

    # Opening line
    if food_action and cab_action:
        lines.append("Working late? Got you covered.")
    elif food_action:
        lines.append("Here's what I found:")
    elif cab_action:
        lines.append("Ready to head home?")
    lines.append("")

    # Food links
    if food_action:
        lines.append("🍕 Order food:")
        for link in (food_action.get("links") or [])[:3]:
            name = link.get("name", "")
            swiggy = link.get("swiggy", "")
            zomato = link.get("zomato", "")
            lines.append(f"• {name}: {swiggy} | {zomato}")
        lines.append("")

    # Cab links
    if cab_action:
        uber = cab_action.get("uber_url", "")
        ola = cab_action.get("ola_url", "")
        lines.append(f"🚗 Book ride home: {uber} | {ola}")
        lines.append("")

    # Reschedule suggestion for evening events
    if reschedule_action:
        events = reschedule_action.get("events") or []
        if events:
            event_list = ", ".join(events)
            lines.append(f"Want me to move your {event_list} to tomorrow?")
            lines.append("Reply 'yes' to reschedule.")

    return "\n".join(lines).strip() or "I'm here to help! What do you need?"


_SCHEDULE_QUERY_PHRASES = [
    "today", "schedule", "what's on", "day like", "show", "calendar",
    "whats my", "what is my", "check my", "my day", "today's",
    "todays", "what do i have", "what have i got", "show me",
    "how does my", "what's happening", "whats happening",
    "morning look", "day look", "week look", "remind me"
]

def _build_onboarding_message(state: PlanBState) -> str:
    oauth_url = state.get("oauth_url") or ""
    if not oauth_url:
        import os
        base_url = os.getenv("API_GATEWAY_URL", "http://localhost:8000")
        user_phone = state.get("user_phone") or ""
        oauth_url = f"{base_url}/auth?phone={quote(user_phone)}"
    return (
        "👋 Welcome to PlanB!\n"
        "\n"
        "I'm your AI scheduling assistant. When your day breaks, I fix it.\n"
        "\n"
        f"Tap here to connect your Google Calendar:\n{oauth_url}\n"
        "\n"
        "Takes 30 seconds. Then just text me what's disrupting your day."
    )


def _format_events_as_day(events: list) -> str:
    """Format a list of Google Calendar events into the standard day view.

    Returns the formatted string. Caller is responsible for the empty-list check.
    Events are sorted by start time.
    """
    timed = []
    for e in events:
        start_str = e.get("start", "")
        name = e.get("summary", "(No title)")
        try:
            dt = datetime.fromisoformat(start_str)
            h = dt.hour % 12 or 12
            ampm = "AM" if dt.hour < 12 else "PM"
            time_str = f"{h}:{dt.minute:02d} {ampm}"
        except (ValueError, TypeError):
            dt = None
            time_str = start_str
        timed.append((dt, name, time_str))
        def _safe_dt(dt):
            if dt is None:
                return datetime.max.replace(tzinfo=None)
            if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt

    timed.sort(key=lambda x: (x[0] is None, _safe_dt(x[0])))
    lines = ["📅 Here's your day:"]
    for _dt, name, time_str in timed:
        lines.append(f"• {time_str} — {name}")
    lines.append("Reply with any disruptions and I'll rebuild your schedule.")
    return "\n".join(lines)


_SCHEDULING_VERBS = (
    "schedule", "add", "book", "set up", "fit in", "squeeze in",
    "make time", "wanna", "want to", "need to",
    "pencil in", "slot in", "arrange",
)
_SCHEDULABLE_ITEMS = (
    "lunch", "dinner", "breakfast", "coffee", "meeting", "call",
    "gym", "workout", "appointment", "session", "hangout",
    "catch up", "drinks", "brunch", "date", "outing",
)


def _is_schedule_request(message_lower: str) -> bool:
    """Return True if the message is a request to schedule something new."""
    has_verb = any(v in message_lower for v in _SCHEDULING_VERBS)
    has_item = any(n in message_lower for n in _SCHEDULABLE_ITEMS)
    has_anti = any(a in message_lower for a in ("cancel", "postpone", "drop", "remove", "delete"))
    return has_verb and has_item and not has_anti


def _format_time(iso_str: str) -> str:
    """Format an ISO time string to readable 12-hour format."""
    try:
        clean = iso_str.replace("+05:30", "").strip()
        dt = datetime.fromisoformat(clean)
        h = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        return f"{h}:{dt.minute:02d} {ampm}"
    except (ValueError, TypeError):
        return iso_str


def _pick_best_slot(free_slots: list, preferred_time: str) -> dict:
    """Pick the free slot closest to the user's preferred time."""
    import re as _re

    pref = preferred_time.lower()

    # Map preference to a target hour
    target_hour = None
    if "breakfast" in pref or "early morning" in pref:
        target_hour = 8
    elif "morning" in pref:
        target_hour = 10
    elif "lunch" in pref or "noon" in pref or "midday" in pref:
        target_hour = 12
    elif "afternoon" in pref:
        target_hour = 14
    elif "evening" in pref or "dinner" in pref or "supper" in pref:
        target_hour = 18
    elif "night" in pref:
        target_hour = 20
    else:
        m = _re.search(r"(\d{1,2})\s*(am|pm)", pref, _re.IGNORECASE)
        if m:
            hour = int(m.group(1))
            if m.group(2).lower() == "pm" and hour != 12:
                hour += 12
            elif m.group(2).lower() == "am" and hour == 12:
                hour = 0
            target_hour = hour

    if target_hour is None:
        # No preference — pick first slot after current time
        now = datetime.now()
        for slot in free_slots:
            try:
                slot_dt = datetime.fromisoformat(slot["start"].replace("+05:30", ""))
                if slot_dt > now:
                    return slot
            except (ValueError, TypeError):
                continue
        return free_slots[0]

    # Find slot closest to target hour
    best = free_slots[0]
    best_diff = float("inf")
    for slot in free_slots:
        try:
            slot_dt = datetime.fromisoformat(slot["start"].replace("+05:30", ""))
            diff = abs(slot_dt.hour - target_hour)
            if diff < best_diff:
                best_diff = diff
                best = slot
        except (ValueError, TypeError):
            continue
    return best


def _handle_schedule_request(state: PlanBState, raw_lower: str) -> str:
    """Parse a scheduling request, find a free slot, create the event, and report."""
    import json as _json

    # Step 1 — Use LLM to parse the request into structured data
    parse_prompt = (
        "Extract the event details from this scheduling request.\n"
        "Return ONLY valid JSON with no extra text:\n"
        '{"event_name": "short descriptive name for the calendar event",'
        ' "preferred_time": "time hint like morning/lunch/afternoon/evening or specific like 2pm, or any if not specified",'
        ' "duration_minutes": 60}\n\n'
        f"Request: {raw_lower}"
    )

    event_name = "New Event"
    preferred_time = "any"
    duration = 60

    try:
        llm = ChatGroq(model=GROQ_MODEL_FAST, api_key=GROQ_API_KEY)
        resp = llm.invoke(parse_prompt)
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        parsed = _json.loads(text)
        event_name = parsed.get("event_name", event_name)
        preferred_time = parsed.get("preferred_time", preferred_time)
        duration = parsed.get("duration_minutes", duration)
    except Exception as e:
        print(f"Comms Agent: schedule request parse failed, using fallback: {e}")
        for item in _SCHEDULABLE_ITEMS:
            if item in raw_lower:
                event_name = item.capitalize()
                break

    # Step 2 — Find free slots for today
    phone = state.get("user_phone")
    today_str = datetime.now().strftime("%Y-%m-%d")
    free_slots = get_free_slots(today_str, duration, phone=phone)

    if not free_slots:
        real_events = get_todays_events(phone=phone)
        lines = [f"Can't fit '{event_name}' ({duration} min) into today — no free slots left."]
        lines.append("")
        if real_events:
            lines.append("Here's what's filling your day:")
            for e in real_events:
                lines.append(f"  {_format_time(e.get('start', ''))} — {e.get('summary', '(No title)')}")
            lines.append("")
        lines.append("Want me to move something to make room? Tell me what's flexible.")
        return "\n".join(lines)

    # Step 3 — Pick the best slot based on preferred time
    best_slot = _pick_best_slot(free_slots, preferred_time)

    # Step 4 — Create the event on Google Calendar
    result = create_event(event_name, best_slot["start"], best_slot["end"], phone=phone)
    if not result:
        return f"Found a slot for '{event_name}' but couldn't create the event. Please try again."

    # Step 5 — Show confirmation with updated calendar
    start_fmt = _format_time(best_slot["start"])
    end_fmt = _format_time(best_slot["end"])

    lines = [f"Done! '{event_name}' is now on your calendar: {start_fmt} to {end_fmt}."]
    lines.append("")

    updated = get_todays_events(phone=phone)
    if updated:
        lines.append("Your updated day:")
        for e in updated:
            lines.append(f"  {_format_time(e.get('start', ''))} — {e.get('summary', '(No title)')}")
        lines.append("")

    lines.append("Reply 'undo' to revert.")
    return "\n".join(lines)


def _build_on_demand_message(state: PlanBState) -> str:
    if state.get("disruption_raw") == "CALENDAR_CONNECT_REQUEST":
        return (
            "To connect your Google Calendar, your admin needs to add "
            "your email to the PlanB OAuth consent screen. "
            "Share your Gmail address and you'll be connected within minutes."
        )

    if state.get("disruption_raw") == "BUFFER_REQUEST":
        return (
            "Got it. I've noted buffer time for tomorrow.\n"
            "Your calendar will be updated with breathing room between tasks."
        )

    raw_lower = (state.get("disruption_raw") or "").lower()

    # Schedule request — user wants to add a new event
    # Must check before the calendar guard and query check
    if _is_schedule_request(raw_lower):
        return _handle_schedule_request(state, raw_lower)

    # Guard — always read real calendar first; LLM must never generate the event list
    real_events = get_todays_events(phone=state.get("user_phone"))
    if not real_events:
        return "Nothing on your calendar today. Add events and I'll help you manage them."

    # Schedule query — return real calendar data directly, no LLM involvement
    if any(phrase in raw_lower for phrase in _SCHEDULE_QUERY_PHRASES):
        return _format_events_as_day(real_events)

    # Actual on-demand scheduling result — validate and format confirmed changes
    confirmed = state.get("confirmed_schedule") or []
    if not confirmed:
        return "Your request has been processed. No schedule changes were needed."

    from utils.scheduling_rules import validate_schedule_item as _validate_schedule_item
    validated = []
    for t in confirmed:
        item = {
            "action": "move",
            "task_name": t.get("task_name", ""),
            "suggested_time": t.get("new_time", ""),
            "old_time": t.get("old_time", ""),
        }
        checked = _validate_schedule_item(item)
        if checked.get("action") == "keep":
            t = dict(t)
            t["new_time"] = t.get("old_time") or t.get("new_time") or "TBD"
        validated.append(t)

    lines = ["Done! Here's what was scheduled:"]
    for t in validated:
        new_time = t.get("new_time") or "TBD"
        lines.append(f"• {t['task_name']} scheduled at {new_time}")
    return "\n".join(lines)


# ── Main agent ─────────────────────────────────────────────────────────────────

def comms_agent(state: PlanBState) -> PlanBState:
    """Comms Agent — always the last agent to run. Formats and sends the final WhatsApp message.

    Builds a mode-appropriate message from state, polishes it with Groq to stay
    under 300 words and plain text, then sends it via WhatsApp. If Groq polishing
    fails, the raw message is sent anyway so the user always gets a response.

    Reads from state:
        mode, context_summary, disruption_raw, confirmed_schedule, routine_decisions,
        emails_sent, deadline_risks, predictive_risks, user_phone.

    Writes to state:
        whatsapp_message (str):   The final message that was sent.
        pipeline_complete (bool): Always set to True.
    """
    mode = state.get("mode") or "on_demand"

    # STEP 1 — Build raw message
    try:
        if mode == "onboarding":
            raw_message = _build_onboarding_message(state)
        elif mode == "undo":
            raw_message = _build_undo_message(state)
        elif mode == "stress":
            raw_message = _build_stress_message(state)
        elif mode == "crisis":
            raw_message = _build_crisis_message(state)
        elif mode == "disruption":
            raw_message = _build_disruption_message(state)
        elif mode == "morning_briefing":
            raw_message = _build_morning_briefing_message(state)
        elif mode == "evening_review":
            raw_message = _build_evening_review_message(state)
        elif mode == "query":
            raw_message = _build_query_message(state)
        elif mode == "lifestyle":
            raw_message = _build_lifestyle_message(state)
        else:
            raw_message = _build_on_demand_message(state)
    except Exception as e:
        print(f"Comms Agent: error building message for mode '{mode}': {e}")
        raw_message = "Your schedule has been updated. Please check your calendar."

    # STEP 2 — Polish with Groq (failure-safe)
    # Skip polishing for modes that return structured real-calendar data:
    # - lifestyle: contains URLs that Groq would mangle
    # - query / on_demand: contain real event names/times that must not be altered
    polished_message = raw_message
    if mode not in ("lifestyle", "query", "on_demand", "onboarding"):
        try:
            llm = ChatGroq(model=GROQ_MODEL_FAST, api_key=GROQ_API_KEY)
            prompt = POLISH_PROMPT.format(raw_message=raw_message)
            response = llm.invoke(prompt)
            polished_message = response.content.strip()
        except Exception as e:
            print(f"Comms Agent: Groq polishing failed, sending raw message: {e}")

    # STEP 3 — Send the message
    user_phone = state.get("user_phone")
    try:
        if user_phone:
            send_message(user_phone, polished_message)
        else:
            print(f"[Comms Agent — no phone, printing to console]\n{polished_message}")
    except Exception as e:
        print(f"Comms Agent: failed to send WhatsApp message: {e}")

    # STEP 4 — Write to state
    state["whatsapp_message"] = polished_message
    state["pipeline_complete"] = True
    return state
