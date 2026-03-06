from datetime import datetime, timedelta

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from config.settings import GROQ_MODEL_FAST, GROQ_API_KEY
from state import PlanBState
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
Message: {raw_message}\
"""


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
        events = get_todays_events()
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
        events = get_events_range(2)
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

    # Your day at a glance — top 3 events by score with times
    try:
        from utils.google_calendar import get_todays_events
        events = get_todays_events()
    except Exception:
        events = []
    task_scores = state.get("task_scores") or {}
    scored_with_time = []
    for e in events:
        eid = e.get("id")
        score = task_scores.get(eid)
        if score is None:
            continue
        name = e.get("summary", "(No title)")
        start_str = e.get("start", "")
        try:
            dt = datetime.fromisoformat(start_str)
            h = dt.hour % 12 or 12
            ampm = "am" if dt.hour < 12 else "pm"
            time_str = f"{h}:{dt.minute:02d}{ampm}"
        except (ValueError, TypeError):
            time_str = start_str
        scored_with_time.append((name, score, time_str))
    scored_with_time.sort(key=lambda x: x[1], reverse=True)
    top3 = scored_with_time[:3]

    if top3:
        lines.append("Your day at a glance:")
        for name, _score, time_str in top3:
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

    # Focus on — single most important task
    if top3:
        lines.append(f"Focus on: {top3[0][0]}")
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
            stats = get_all_habit_stats()
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

    return raw or "No information available."


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


def _build_on_demand_message(state: PlanBState) -> str:
    if state.get("disruption_raw") == "BUFFER_REQUEST":
        return (
            "Got it. I've noted buffer time for tomorrow.\n"
            "Your calendar will be updated with breathing room between tasks."
        )
    confirmed = state.get("confirmed_schedule") or []
    if not confirmed:
        return "Your request has been processed. No schedule changes were needed."
    lines = ["Done! Here's what was scheduled:"]
    for t in confirmed:
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
        if mode == "undo":
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
    # Lifestyle messages contain URLs and emoji that Groq would mangle — skip polishing.
    polished_message = raw_message
    if mode != "lifestyle":
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
