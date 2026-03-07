"""Tests for agents/comms — message builder functions."""

from unittest.mock import patch

from agents.comms import (
    _build_crisis_message,
    _build_disruption_message,
    _build_query_message,
    _build_morning_briefing_message,
    _build_on_demand_message,
)


class TestBuildCrisisMessage:
    def test_header_always_present(self):
        msg = _build_crisis_message({})
        assert "CRISIS MODE ACTIVATED" in msg

    def test_footer_always_present(self):
        msg = _build_crisis_message({})
        assert "Focus on what matters" in msg

    def test_dropped_tasks_section(self):
        state = {
            "crisis_actions": [
                {"action": "dropped", "task_name": "Gym", "reason": "score 30 < 50"},
                {"action": "dropped", "task_name": "Reading", "reason": "score 45 < 50"},
            ]
        }
        msg = _build_crisis_message(state)
        assert "Dropped low-priority tasks" in msg
        assert "Gym" in msg
        assert "Reading" in msg

    def test_no_dropped_tasks_section_when_empty(self):
        state = {"crisis_actions": []}
        msg = _build_crisis_message(state)
        assert "Dropped low-priority tasks" not in msg

    def test_dnd_calendar_block_shown(self):
        state = {
            "crisis_actions": [
                {
                    "action": "calendar_block_created",
                    "summary": "PlanB: CRISIS - Do Not Disturb",
                    "start": "2026-03-05T14:00:00",
                    "end": "2026-03-05T17:00:00",
                }
            ]
        }
        msg = _build_crisis_message(state)
        assert "Calendar block created" in msg

    def test_emails_shown(self):
        state = {
            "crisis_actions": [
                {"action": "dnd_email", "to": "boss@corp.com", "meeting": "Standup", "status": "sent"},
                {"action": "dnd_email", "to": "peer@corp.com", "meeting": "Standup", "status": "sent"},
            ]
        }
        msg = _build_crisis_message(state)
        assert "boss@corp.com" in msg or "peer@corp.com" in msg
        assert "DND notice emails sent" in msg

    def test_failed_emails_not_listed(self):
        state = {
            "crisis_actions": [
                {"action": "dnd_email", "to": "boss@corp.com", "meeting": "X", "status": "failed"},
            ]
        }
        msg = _build_crisis_message(state)
        # failed emails should not appear in the sent list
        assert "boss@corp.com" not in msg

    def test_no_crisis_actions_key(self):
        msg = _build_crisis_message({})
        assert "CRISIS MODE ACTIVATED" in msg

    def test_none_crisis_actions(self):
        msg = _build_crisis_message({"crisis_actions": None})
        assert "CRISIS MODE ACTIVATED" in msg


class TestBuildDisruptionMessageStreakProtection:
    def test_streak_protected_notice_shown(self):
        state = {
            "routine_decisions": {
                "Gym": {
                    "decision": "kept",
                    "streak_protected": True,
                    "drop_count": 2,
                    "event_id": "x",
                }
            }
        }
        msg = _build_disruption_message(state)
        assert "Streak protection" in msg
        assert "Gym" in msg

    def test_streak_protected_false_not_shown(self):
        state = {
            "routine_decisions": {
                "Gym": {"decision": "kept", "streak_protected": False, "drop_count": 0, "event_id": "x"}
            }
        }
        msg = _build_disruption_message(state)
        assert "Streak protection" not in msg

    def test_streak_protected_key_absent_not_shown(self):
        state = {
            "routine_decisions": {
                "Gym": {"decision": "kept", "event_id": "x"}
            }
        }
        msg = _build_disruption_message(state)
        assert "Streak protection" not in msg

    def test_multiple_streak_protected_all_shown(self):
        state = {
            "routine_decisions": {
                "Gym": {"decision": "kept", "streak_protected": True, "drop_count": 3, "event_id": "a"},
                "Reading": {"decision": "kept", "streak_protected": True, "drop_count": 2, "event_id": "b"},
            }
        }
        msg = _build_disruption_message(state)
        assert "Gym" in msg
        assert "Reading" in msg

    def test_kept_section_still_present_with_streak(self):
        state = {
            "routine_decisions": {
                "Gym": {"decision": "kept", "streak_protected": True, "drop_count": 2, "event_id": "x"}
            }
        }
        msg = _build_disruption_message(state)
        # Both "What stayed" kept section and streak section should appear
        assert "Gym" in msg


class TestBuildQueryMessage:
    def test_habit_stats_request_no_data(self):
        with patch("utils.habit_learner.get_all_habit_stats", return_value={}):
            msg = _build_query_message({"disruption_raw": "HABIT_STATS_REQUEST"})
        assert "No habit data" in msg

    def test_habit_stats_request_with_data(self):
        fake = {
            "Gym": {"times_kept": 18, "times_dropped": 3, "total": 21, "score_boost": 15},
            "Reading": {"times_kept": 12, "times_dropped": 9, "total": 21, "score_boost": 0},
        }
        with patch("utils.habit_learner.get_all_habit_stats", return_value=fake):
            msg = _build_query_message({"disruption_raw": "HABIT_STATS_REQUEST"})

        assert "Your habit stats" in msg
        assert "Gym" in msg
        assert "18/21" in msg
        assert "+15" in msg
        assert "Reading" in msg
        assert "12/21" in msg

    def test_habit_stats_no_boost_shown_when_zero(self):
        fake = {"Reading": {"times_kept": 5, "times_dropped": 2, "total": 7, "score_boost": 0}}
        with patch("utils.habit_learner.get_all_habit_stats", return_value=fake):
            msg = _build_query_message({"disruption_raw": "HABIT_STATS_REQUEST"})
        # No "+0" noise
        assert "+0" not in msg

    def test_regular_query_passthrough(self):
        msg = _build_query_message({"disruption_raw": "What is my schedule today?"})
        assert msg == "What is my schedule today?"

    def test_empty_raw_fallback(self):
        msg = _build_query_message({})
        assert "No information available" in msg

    def test_habit_stats_fetch_error_handled(self):
        with patch("utils.habit_learner.get_all_habit_stats", side_effect=Exception("S3 down")):
            msg = _build_query_message({"disruption_raw": "HABIT_STATS_REQUEST"})
        assert "Could not load" in msg


class TestCommsAgentModeDispatch:
    """Test that comms_agent selects the right builder per mode."""

    def _run_comms(self, mode, extra_state=None):
        from unittest.mock import patch as p
        from agents.comms import comms_agent
        from state import get_initial_state
        state = get_initial_state()
        state["mode"] = mode
        state["disruption_raw"] = "test"
        if extra_state:
            state.update(extra_state)
        with p("agents.comms.send_message"), p("agents.comms.ChatGroq") as mock_llm:
            mock_llm.return_value.invoke.return_value.content = "polished"
            return comms_agent(state)

    def test_crisis_mode_sets_pipeline_complete(self):
        r = self._run_comms("crisis", {"crisis_actions": []})
        assert r["pipeline_complete"] is True

    def test_disruption_mode_sets_pipeline_complete(self):
        r = self._run_comms("disruption")
        assert r["pipeline_complete"] is True

    def test_whatsapp_message_always_set(self):
        r = self._run_comms("query")
        assert r["whatsapp_message"] is not None
