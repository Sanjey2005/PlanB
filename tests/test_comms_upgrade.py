"""Tests for comms upgrade: helpers, morning briefing, evening review, buffer command."""

from unittest.mock import patch
from datetime import datetime, timedelta

import pytest

from state import get_initial_state
from agents.comms import (
    _day_of_week_closer,
    _scored_events_today,
    _format_today_risk,
    _build_morning_briefing_message,
    _build_evening_review_message,
    _build_on_demand_message,
)
from agents.monitor import monitor_agent

TODAY = datetime.now().strftime("%Y-%m-%d")
TOMORROW = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

_EVENTS = [
    {"id": "e1", "summary": "Deep Work", "start": "2026-03-05T09:00:00+05:30"},
    {"id": "e2", "summary": "Team Standup", "start": "2026-03-05T10:00:00+05:30"},
    {"id": "e3", "summary": "Gym", "start": "2026-03-05T07:00:00+05:30"},
]


# ── _day_of_week_closer ────────────────────────────────────────────────────────

class TestDayOfWeekCloser:
    def test_monday_returns_new_week(self):
        with patch("agents.comms.datetime") as mock_dt:
            mock_dt.now.return_value.weekday.return_value = 0
            mock_dt.now.return_value.strftime = datetime.now().strftime
            result = _day_of_week_closer()
        assert "New week" in result

    def test_friday_returns_final_push(self):
        with patch("agents.comms.datetime") as mock_dt:
            mock_dt.now.return_value.weekday.return_value = 4
            mock_dt.now.return_value.strftime = datetime.now().strftime
            result = _day_of_week_closer()
        assert "Final push" in result

    def test_other_days_generic_message(self):
        for day in [1, 2, 3, 5, 6]:
            with patch("agents.comms.datetime") as mock_dt:
                mock_dt.now.return_value.weekday.return_value = day
                mock_dt.now.return_value.strftime = datetime.now().strftime
                result = _day_of_week_closer()
            assert "Focus on what matters" in result

    def test_returns_string(self):
        result = _day_of_week_closer()
        assert isinstance(result, str)
        assert len(result) > 0


# ── _scored_events_today ───────────────────────────────────────────────────────

class TestScoredEventsToday:
    def test_returns_top_n_sorted_descending(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90, "e2": 60, "e3": 45}
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            result = _scored_events_today(state, n=3)
        assert result[0] == ("Deep Work", 90)
        assert result[1] == ("Team Standup", 60)
        assert result[2] == ("Gym", 45)

    def test_returns_at_most_n(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90, "e2": 60, "e3": 45}
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            result = _scored_events_today(state, n=2)
        assert len(result) == 2

    def test_unscored_events_excluded(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90}  # e2, e3 have no score
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            result = _scored_events_today(state, n=3)
        assert len(result) == 1
        assert result[0][0] == "Deep Work"

    def test_empty_task_scores_returns_empty(self):
        state = get_initial_state()
        state["task_scores"] = {}
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            result = _scored_events_today(state, n=3)
        assert result == []

    def test_none_task_scores_returns_empty(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            result = _scored_events_today(state, n=3)
        assert result == []

    def test_calendar_failure_returns_empty(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90}
        with patch("utils.google_calendar.get_todays_events", side_effect=Exception("calendar down")):
            result = _scored_events_today(state, n=3)
        assert result == []

    def test_returns_list_of_tuples(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90}
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            result = _scored_events_today(state, n=3)
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)


# ── _format_today_risk ─────────────────────────────────────────────────────────

class TestFormatTodayRisk:
    def test_burnout_type(self):
        risk = {"type": "burnout", "detail": "5 days of high load"}
        result = _format_today_risk(risk, [])
        assert "Burnout risk" in result
        assert "5 days of high load" in result

    def test_deadline_compression_type(self):
        risk = {"type": "deadline_compression", "detail": "Project due tomorrow"}
        result = _format_today_risk(risk, [])
        assert "Deadline pressure" in result
        assert "Project due tomorrow" in result

    def test_energy_misalignment_type(self):
        risk = {"type": "energy_misalignment", "detail": "Deep work after 8pm"}
        result = _format_today_risk(risk, [])
        assert "Energy mismatch" in result

    def test_missing_buffer_type(self):
        risk = {"type": "missing_buffer", "detail": "No gaps between back-to-back meetings"}
        result = _format_today_risk(risk, [])
        assert "No buffer time" in result

    def test_overload_type_with_scored_desc(self):
        risk = {"type": "overload", "detail": "9 tasks today"}
        scored = [("Deep Work", 90), ("Standup", 60), ("Gym", 20)]
        result = _format_today_risk(risk, scored)
        assert "Overloaded day" in result
        assert "Gym" in result  # lowest score task

    def test_overload_type_no_scored_desc(self):
        risk = {"type": "overload", "detail": "9 tasks today"}
        result = _format_today_risk(risk, [])
        assert "Overloaded day" in result
        # No task name suggestion when scored is empty

    def test_unknown_type_fallback(self):
        risk = {"type": "some_new_type", "detail": "Something weird"}
        result = _format_today_risk(risk, [])
        assert "Risk:" in result
        assert "Something weird" in result

    def test_uses_description_fallback(self):
        risk = {"type": "burnout", "description": "backup detail"}
        result = _format_today_risk(risk, [])
        assert "backup detail" in result

    def test_all_types_start_with_warning_symbol(self):
        types = ["burnout", "deadline_compression", "energy_misalignment", "missing_buffer", "overload"]
        for t in types:
            risk = {"type": t, "detail": "detail"}
            result = _format_today_risk(risk, [("Task", 50)])
            assert "\u26a0" in result


# ── _build_morning_briefing_message ───────────────────────────────────────────

class TestBuildMorningBriefingMessage:
    def test_morning_greeting_before_9am(self):
        state = get_initial_state()
        with patch("agents.comms.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 7
            mock_dt.now.return_value.strftime.return_value = TODAY
            mock_dt.fromisoformat = datetime.fromisoformat
            with patch("utils.google_calendar.get_todays_events", return_value=[]):
                msg = _build_morning_briefing_message(state)
        assert "Good morning!" in msg

    def test_morning_greeting_at_9am_or_later(self):
        state = get_initial_state()
        with patch("agents.comms.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 10
            mock_dt.now.return_value.strftime.return_value = TODAY
            mock_dt.fromisoformat = datetime.fromisoformat
            with patch("utils.google_calendar.get_todays_events", return_value=[]):
                msg = _build_morning_briefing_message(state)
        assert "Morning!" in msg

    def test_energy_check_none_fatigue(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "You're starting fresh today." in msg

    def test_energy_check_low_fatigue(self):
        state = get_initial_state()
        state["fatigue_level"] = "low"
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "You're starting fresh today." in msg

    def test_energy_check_medium_fatigue(self):
        state = get_initial_state()
        state["fatigue_level"] = "medium"
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Pacing yourself today" in msg

    def test_energy_check_high_fatigue(self):
        state = get_initial_state()
        state["fatigue_level"] = "high"
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Take it easy today" in msg

    def test_day_at_a_glance_section_with_scores(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90, "e2": 60, "e3": 45}
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            msg = _build_morning_briefing_message(state)
        assert "Your day at a glance:" in msg
        assert "Deep Work" in msg

    def test_day_at_a_glance_shows_time(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90}
        with patch("utils.google_calendar.get_todays_events", return_value=[_EVENTS[0]]):
            msg = _build_morning_briefing_message(state)
        assert "9:00am" in msg

    def test_no_glance_section_when_no_scores(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS):
            msg = _build_morning_briefing_message(state)
        assert "Your day at a glance:" not in msg

    def test_watch_out_section_with_today_risk(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "high", "date": TODAY, "detail": "High load this week"},
        ]
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Watch out:" in msg
        assert "High load this week" in msg

    def test_watch_out_shows_only_one_risk(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "high", "date": TODAY, "detail": "Risk A"},
            {"type": "overload", "severity": "medium", "date": TODAY, "detail": "Risk B"},
        ]
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert msg.count("Watch out:") == 1
        assert "Risk A" in msg

    def test_no_watch_out_when_low_severity(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "low", "date": TODAY, "detail": "Minor load"},
        ]
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Watch out:" not in msg

    def test_tomorrow_risks_not_in_morning_briefing(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "high", "date": TOMORROW, "detail": "Tomorrow issue"},
        ]
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Tomorrow issue" not in msg

    def test_focus_on_section_with_top_task(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 90, "e2": 60}
        with patch("utils.google_calendar.get_todays_events", return_value=_EVENTS[:2]):
            msg = _build_morning_briefing_message(state)
        assert "Focus on: Deep Work" in msg

    def test_no_focus_on_when_no_scored_events(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Focus on:" not in msg

    def test_day_closer_present(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        closers = ["New week", "Final push", "Focus on what matters"]
        assert any(c in msg for c in closers)

    def test_no_have_a_great_day(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Have a great day" not in msg

    def test_no_watch_out_when_no_risks(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", return_value=[]):
            msg = _build_morning_briefing_message(state)
        assert "Watch out:" not in msg

    def test_calendar_failure_still_returns_message(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_todays_events", side_effect=Exception("down")):
            msg = _build_morning_briefing_message(state)
        assert "morning" in msg.lower()


# ── _build_evening_review_message ─────────────────────────────────────────────

class TestBuildEveningReviewMessage:
    def test_day_wrap_opener(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Day wrap:" in msg

    def test_rest_well_closer(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Rest well." in msg

    def test_no_routine_tasks_fallback(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "No routine tasks recorded today." in msg

    def test_kept_routine_tasks_listed(self):
        state = get_initial_state()
        state["routine_decisions"] = {
            "Gym": {"decision": "kept"},
            "Reading": {"decision": "dropped"},
        }
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Gym" in msg
        assert "Reading" not in msg

    def test_what_moved_section_with_rescheduled_task(self):
        state = get_initial_state()
        state["confirmed_schedule"] = [
            {"task_name": "Deep Work", "old_time": "9:00", "new_time": "14:00", "reason": "conflict"},
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "What moved:" in msg
        assert "Deep Work" in msg
        assert "9:00" in msg
        assert "14:00" in msg

    def test_no_what_moved_when_no_old_time(self):
        state = get_initial_state()
        state["confirmed_schedule"] = [
            {"task_name": "Gym", "new_time": "19:00"},
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "What moved:" not in msg

    def test_tomorrow_section_present(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Tomorrow:" in msg

    def test_tomorrow_nothing_scheduled(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Nothing scheduled yet." in msg

    def test_risk_to_watch_section(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "high", "date": TOMORROW, "detail": "Heavy schedule tomorrow"},
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Risk to watch:" in msg
        assert "Heavy schedule tomorrow" in msg

    def test_no_risk_to_watch_when_empty(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Risk to watch:" not in msg

    def test_today_risk_not_shown_in_evening(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "high", "date": TODAY, "detail": "Today's issue"},
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Today's issue" not in msg

    def test_buffer_it_shown_for_missing_buffer_risk(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {
                "type": "missing_buffer",
                "severity": "high",
                "date": TOMORROW,
                "detail": "Back-to-back meetings",
                "intervention": "Add 15-min gaps between meetings",
            },
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Risk to watch: Back-to-back meetings" in msg
        assert "buffer it" in msg

    def test_intervention_shown_for_non_buffer_risk(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {
                "type": "burnout",
                "severity": "high",
                "date": TOMORROW,
                "detail": "Heavy load",
                "intervention": "Take a break tomorrow morning",
            },
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Tip: Take a break tomorrow morning" in msg
        assert "buffer it" not in msg

    def test_buffer_it_not_shown_for_non_buffer_risk(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "high", "date": TOMORROW, "detail": "High load"},
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Reply 'buffer it'" not in msg

    def test_low_severity_tomorrow_risks_excluded(self):
        state = get_initial_state()
        state["predictive_risks"] = [
            {"type": "burnout", "severity": "low", "date": TOMORROW, "detail": "Minor load"},
        ]
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Risk to watch:" not in msg

    def test_none_predictive_risks(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Risk to watch:" not in msg

    def test_no_evening_review_header(self):
        state = get_initial_state()
        with patch("utils.google_calendar.get_events_range", return_value=[]):
            msg = _build_evening_review_message(state)
        assert "Evening review" not in msg


# ── _build_on_demand_message with BUFFER_REQUEST ───────────────────────────────

class TestBuildOnDemandBufferRequest:
    def test_buffer_request_confirmation(self):
        state = get_initial_state()
        state["disruption_raw"] = "BUFFER_REQUEST"
        msg = _build_on_demand_message(state)
        assert "buffer time" in msg.lower()

    def test_buffer_response_mentions_calendar(self):
        state = get_initial_state()
        state["disruption_raw"] = "BUFFER_REQUEST"
        msg = _build_on_demand_message(state)
        assert "calendar" in msg.lower()

    def test_normal_on_demand_unaffected(self):
        state = get_initial_state()
        state["confirmed_schedule"] = [{"task_name": "Gym", "new_time": "18:00"}]
        msg = _build_on_demand_message(state)
        assert "Gym" in msg
        assert "buffer" not in msg.lower()

    def test_no_confirmed_no_buffer_request(self):
        state = get_initial_state()
        msg = _build_on_demand_message(state)
        assert "processed" in msg.lower()


# ── Monitor BUFFER_KEYWORDS ────────────────────────────────────────────────────

def _user_msg(text: str) -> dict:
    s = get_initial_state()
    s["disruption_source"] = "user_message"
    s["disruption_raw"] = text
    return s


class TestBufferKeywords:
    def test_buffer_it(self):
        r = monitor_agent(_user_msg("buffer it"))
        assert r["mode"] == "on_demand"
        assert r["disruption_raw"] == "BUFFER_REQUEST"

    def test_add_buffers(self):
        r = monitor_agent(_user_msg("add buffers please"))
        assert r["mode"] == "on_demand"
        assert r["disruption_raw"] == "BUFFER_REQUEST"

    def test_buffer_it_case_insensitive(self):
        r = monitor_agent(_user_msg("BUFFER IT"))
        assert r["disruption_raw"] == "BUFFER_REQUEST"

    def test_buffer_request_does_not_set_crisis_mode(self):
        r = monitor_agent(_user_msg("buffer it"))
        assert r.get("crisis_mode") is None

    def test_buffer_request_does_not_set_stress_mode(self):
        r = monitor_agent(_user_msg("buffer it"))
        assert r.get("stress_mode") is None

    def test_buffer_beats_query_keywords(self):
        # "show" is a query keyword, but buffer detection fires first
        r = monitor_agent(_user_msg("show me how to buffer it"))
        assert r["disruption_raw"] == "BUFFER_REQUEST"

    def test_non_buffer_on_demand_unaffected(self):
        r = monitor_agent(_user_msg("add gym tomorrow"))
        assert r.get("disruption_raw") != "BUFFER_REQUEST"
