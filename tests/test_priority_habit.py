"""Tests for agents/priority — habit learning score adjustments."""

from unittest.mock import patch, MagicMock

from state import get_initial_state
from agents.priority import priority_engine

_EVENTS = [
    {
        "id": "e1", "summary": "Gym",
        "start": "2026-03-05T07:00:00+05:30", "end": "2026-03-05T08:00:00+05:30",
        "attendees": [], "extendedProperties": {},
    },
    {
        "id": "e2", "summary": "Team Standup",
        "start": "2026-03-05T10:00:00+05:30", "end": "2026-03-05T10:30:00+05:30",
        "attendees": ["a@b.com"], "extendedProperties": {},
    },
]


def _run_priority(learned_scores, events=None):
    if events is None:
        events = _EVENTS

    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = '{"Gym": "routine", "Team Standup": "meeting"}'

    state = get_initial_state()
    state["fatigue_level"] = "none"

    with patch("agents.priority.get_todays_events", return_value=events), \
         patch("agents.priority.get_events_range", return_value=[]), \
         patch("agents.priority.ChatGroq", return_value=mock_llm), \
         patch("agents.priority.get_learned_scores", return_value=learned_scores):
        return priority_engine(state)


class TestHabitLearningAdjustment:
    def test_positive_adjustment_applied(self):
        r = _run_priority({"Gym": 15, "Team Standup": 0})
        gym_score = r["task_scores"]["e1"]
        # Base score for routine at no fatigue + adjustment >= adjustment
        assert gym_score > 0

    def test_zero_adjustment_not_logged(self):
        # Just checking it doesn't crash with zero adjustments
        r = _run_priority({"Gym": 0, "Team Standup": 0})
        assert "e1" in r["task_scores"]
        assert "e2" in r["task_scores"]

    def test_adjustment_increases_score(self):
        # Run with no adjustment, then with +15 — score must be higher
        r_no_adj = _run_priority({"Gym": 0, "Team Standup": 0})
        r_with_adj = _run_priority({"Gym": 15, "Team Standup": 0})
        assert r_with_adj["task_scores"]["e1"] > r_no_adj["task_scores"]["e1"]

    def test_score_capped_at_100(self):
        # Even with large adjustment, score should not exceed 100
        r = _run_priority({"Gym": 30, "Team Standup": 30})
        for eid, score in r["task_scores"].items():
            assert score <= 100, f"Score {score} exceeds 100 for {eid}"

    def test_score_minimum_zero(self):
        r = _run_priority({"Gym": 0, "Team Standup": 0})
        for eid, score in r["task_scores"].items():
            assert score >= 0

    def test_task_scores_written_to_state(self):
        r = _run_priority({"Gym": 10})
        assert r["task_scores"] is not None
        assert isinstance(r["task_scores"], dict)

    def test_all_event_ids_scored(self):
        r = _run_priority({"Gym": 0, "Team Standup": 0})
        assert "e1" in r["task_scores"]
        assert "e2" in r["task_scores"]

    def test_unknown_task_in_learned_scores_ignored(self):
        # A task name that doesn't match any event should not crash
        r = _run_priority({"NonExistentTask": 20, "Gym": 10})
        assert "e1" in r["task_scores"]

    def test_habit_learning_failure_does_not_break_scoring(self):
        state = get_initial_state()
        state["fatigue_level"] = "none"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = '{"Gym": "routine"}'

        with patch("agents.priority.get_todays_events", return_value=_EVENTS), \
             patch("agents.priority.get_events_range", return_value=[]), \
             patch("agents.priority.ChatGroq", return_value=mock_llm), \
             patch("agents.priority.get_learned_scores", side_effect=Exception("S3 down")):
            result = priority_engine(state)

        # Scores should still be computed even if habit learning fails
        assert "e1" in result["task_scores"]
        assert "e2" in result["task_scores"]


class TestGetLearnedScoresCalled:
    def test_get_learned_scores_called_with_summaries(self):
        mock_learned = MagicMock(return_value={})
        state = get_initial_state()
        state["fatigue_level"] = "none"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = '{"Gym": "routine", "Team Standup": "meeting"}'

        with patch("agents.priority.get_todays_events", return_value=_EVENTS), \
             patch("agents.priority.get_events_range", return_value=[]), \
             patch("agents.priority.ChatGroq", return_value=mock_llm), \
             patch("agents.priority.get_learned_scores", mock_learned):
            priority_engine(state)

        mock_learned.assert_called_once()
        call_args = mock_learned.call_args[0][0]
        assert "Gym" in call_args
        assert "Team Standup" in call_args
