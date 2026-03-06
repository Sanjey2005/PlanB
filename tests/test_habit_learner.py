"""Tests for utils/habit_learner — score adjustments and stats computation.

Cache is cleared before/after each test via conftest.py.
_load_all_logs is patched to avoid S3 calls.
"""

from unittest.mock import patch
import utils.habit_learner as hl


def _make_log(routine_decisions: dict, confirmed_schedule: list = None) -> dict:
    return {
        "routine_decisions": routine_decisions,
        "confirmed_schedule": confirmed_schedule or [],
    }


class TestComputeStats:
    def test_counts_kept(self):
        logs = [
            _make_log({"Gym": {"decision": "kept"}}),
            _make_log({"Gym": {"decision": "kept"}}),
        ]
        stats = hl._compute_stats(logs)
        assert stats["Gym"]["times_kept"] == 2

    def test_counts_dropped(self):
        logs = [
            _make_log({"Gym": {"decision": "dropped"}}),
            _make_log({"Gym": {"decision": "dropped"}}),
            _make_log({"Gym": {"decision": "kept"}}),
        ]
        stats = hl._compute_stats(logs)
        assert stats["Gym"]["times_dropped"] == 2
        assert stats["Gym"]["times_kept"] == 1

    def test_total_counts_all_appearances(self):
        logs = [
            _make_log({"Gym": {"decision": "kept"}}),
            _make_log({"Gym": {"decision": "dropped"}}),
            _make_log({"Gym": {"decision": "compressed"}}),
        ]
        stats = hl._compute_stats(logs)
        assert stats["Gym"]["total"] == 3

    def test_user_override_detected(self):
        # routine said drop, but Gym appears in confirmed_schedule → override
        logs = [
            _make_log(
                {"Gym": {"decision": "dropped"}},
                confirmed_schedule=[{"task_name": "Gym"}],
            )
        ]
        stats = hl._compute_stats(logs)
        assert stats["Gym"]["user_overrides"] == 1

    def test_no_override_when_confirmed_schedule_empty(self):
        logs = [_make_log({"Gym": {"decision": "dropped"}}, confirmed_schedule=[])]
        stats = hl._compute_stats(logs)
        assert stats["Gym"]["user_overrides"] == 0

    def test_no_override_when_decision_not_drop(self):
        logs = [
            _make_log(
                {"Gym": {"decision": "kept"}},
                confirmed_schedule=[{"task_name": "Gym"}],
            )
        ]
        stats = hl._compute_stats(logs)
        assert stats["Gym"]["user_overrides"] == 0

    def test_multiple_tasks_tracked_independently(self):
        logs = [
            _make_log({"Gym": {"decision": "kept"}, "Reading": {"decision": "dropped"}}),
        ]
        stats = hl._compute_stats(logs)
        assert stats["Gym"]["times_kept"] == 1
        assert stats["Gym"]["times_dropped"] == 0
        assert stats["Reading"]["times_dropped"] == 1
        assert stats["Reading"]["times_kept"] == 0

    def test_empty_logs_returns_empty(self):
        assert hl._compute_stats([]) == {}

    def test_log_without_routine_decisions_skipped(self):
        logs = [{"confirmed_schedule": []}]
        stats = hl._compute_stats(logs)
        assert stats == {}


class TestGetLearnedScores:
    def test_five_points_per_override(self):
        logs = [
            _make_log({"Gym": {"decision": "dropped"}}, [{"task_name": "Gym"}]),
            _make_log({"Gym": {"decision": "dropped"}}, [{"task_name": "Gym"}]),
        ]
        with patch.object(hl, "_load_all_logs", return_value=logs):
            scores = hl.get_learned_scores(["Gym"])
        assert scores["Gym"] == 10  # 2 overrides × 5

    def test_cap_at_30(self):
        # 7 overrides × 5 = 35, should be capped at 30
        logs = [
            _make_log({"Gym": {"decision": "dropped"}}, [{"task_name": "Gym"}])
            for _ in range(7)
        ]
        with patch.object(hl, "_load_all_logs", return_value=logs):
            scores = hl.get_learned_scores(["Gym"])
        assert scores["Gym"] == 30

    def test_zero_overrides_zero_adjustment(self):
        logs = [_make_log({"Gym": {"decision": "kept"}})]
        with patch.object(hl, "_load_all_logs", return_value=logs):
            scores = hl.get_learned_scores(["Gym"])
        assert scores["Gym"] == 0

    def test_unknown_task_returns_zero(self):
        with patch.object(hl, "_load_all_logs", return_value=[]):
            scores = hl.get_learned_scores(["UnknownTask"])
        assert scores["UnknownTask"] == 0

    def test_returns_all_requested_tasks(self):
        logs = [_make_log({"Gym": {"decision": "kept"}})]
        with patch.object(hl, "_load_all_logs", return_value=logs):
            scores = hl.get_learned_scores(["Gym", "Reading", "Meditation"])
        assert set(scores.keys()) == {"Gym", "Reading", "Meditation"}


class TestCaching:
    def test_s3_not_called_twice(self):
        logs = [_make_log({"Gym": {"decision": "kept"}})]
        with patch.object(hl, "_load_all_logs", return_value=logs) as mock_load:
            hl.get_learned_scores(["Gym"])
            hl.get_learned_scores(["Gym"])  # second call — should use cache
        mock_load.assert_called_once()

    def test_cache_cleared_between_tests_by_fixture(self):
        # cache should be empty at test start (conftest clears it)
        assert hl._stats_cache == {}
        assert hl._score_cache == {}

    def test_get_all_habit_stats_uses_cache(self):
        logs = [_make_log({"Gym": {"decision": "kept"}})]
        with patch.object(hl, "_load_all_logs", return_value=logs) as mock_load:
            hl.get_learned_scores(["Gym"])   # populates cache
            hl.get_all_habit_stats()          # should not reload
        mock_load.assert_called_once()


class TestGetAllHabitStats:
    def test_returns_dict_with_score_boost(self):
        logs = [
            _make_log({"Gym": {"decision": "dropped"}}, [{"task_name": "Gym"}]),
            _make_log({"Gym": {"decision": "kept"}}),
        ]
        with patch.object(hl, "_load_all_logs", return_value=logs):
            stats = hl.get_all_habit_stats()

        assert "Gym" in stats
        assert "score_boost" in stats["Gym"]
        assert stats["Gym"]["score_boost"] == 5  # 1 override × 5

    def test_returns_times_kept_and_dropped(self):
        logs = [
            _make_log({"Gym": {"decision": "kept"}}),
            _make_log({"Gym": {"decision": "kept"}}),
            _make_log({"Gym": {"decision": "dropped"}}),
        ]
        with patch.object(hl, "_load_all_logs", return_value=logs):
            stats = hl.get_all_habit_stats()

        assert stats["Gym"]["times_kept"] == 2
        assert stats["Gym"]["times_dropped"] == 1

    def test_empty_logs_returns_empty(self):
        with patch.object(hl, "_load_all_logs", return_value=[]):
            stats = hl.get_all_habit_stats()
        assert stats == {}

    def test_load_failure_returns_empty(self):
        with patch.object(hl, "_load_all_logs", side_effect=Exception("S3 down")):
            stats = hl.get_all_habit_stats()
        assert stats == {}
