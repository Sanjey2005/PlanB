"""Tests for utils/streak_tracker — streak and drop count logic.

S3 calls are isolated by patching _load_logs_for_date directly.
"""

from datetime import date, timedelta
from unittest.mock import patch


def _make_log(routine_decisions: dict) -> dict:
    return {"routine_decisions": routine_decisions}


def _dates_back(n: int) -> list:
    today = date.today()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, n + 1)]


class TestGetDropCountLastNDays:
    def test_counts_drops_across_multiple_days(self):
        d = _dates_back(3)
        day_logs = {
            d[0]: [_make_log({"Gym": {"decision": "dropped"}})],
            d[1]: [_make_log({"Gym": {"decision": "dropped"}})],
            d[2]: [_make_log({"Gym": {"decision": "kept"}})],
        }

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_drop_count_last_n_days
            assert get_drop_count_last_n_days("Gym", 3) == 2

    def test_counts_multiple_drops_same_day(self):
        d = _dates_back(1)
        day_logs = {
            d[0]: [
                _make_log({"Gym": {"decision": "dropped"}}),
                _make_log({"Gym": {"decision": "dropped"}}),
            ],
        }

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_drop_count_last_n_days
            assert get_drop_count_last_n_days("Gym", 1) == 2

    def test_zero_drops_returns_zero(self):
        d = _dates_back(2)
        day_logs = {
            d[0]: [_make_log({"Gym": {"decision": "kept"}})],
            d[1]: [_make_log({"Gym": {"decision": "kept"}})],
        }

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_drop_count_last_n_days
            assert get_drop_count_last_n_days("Gym", 2) == 0

    def test_missing_days_return_zero(self):
        with patch("utils.streak_tracker._load_logs_for_date", return_value=[]), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_drop_count_last_n_days
            assert get_drop_count_last_n_days("Gym", 3) == 0

    def test_task_not_in_logs_returns_zero(self):
        d = _dates_back(1)
        day_logs = {d[0]: [_make_log({"Reading": {"decision": "dropped"}})]}

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_drop_count_last_n_days
            assert get_drop_count_last_n_days("Gym", 1) == 0

    def test_only_n_days_scanned(self):
        d = _dates_back(5)
        # Drop only on day 4 and 5 (outside n=3 window)
        day_logs = {
            d[3]: [_make_log({"Gym": {"decision": "dropped"}})],
            d[4]: [_make_log({"Gym": {"decision": "dropped"}})],
        }

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_drop_count_last_n_days
            assert get_drop_count_last_n_days("Gym", 3) == 0


class TestGetStreak:
    def test_streak_of_three(self):
        d = _dates_back(3)
        day_logs = {day: [_make_log({"Gym": {"decision": "kept"}})] for day in d}

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_streak
            assert get_streak("Gym") == 3

    def test_streak_broken_on_first_day(self):
        d = _dates_back(1)
        day_logs = {d[0]: [_make_log({"Gym": {"decision": "dropped"}})]}

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_streak
            assert get_streak("Gym") == 0

    def test_streak_stops_at_gap(self):
        d = _dates_back(4)
        # kept d[0], d[1]; missing d[2]; kept d[3] — streak should be 2
        day_logs = {
            d[0]: [_make_log({"Gym": {"decision": "kept"}})],
            d[1]: [_make_log({"Gym": {"decision": "kept"}})],
            # d[2] missing
            d[3]: [_make_log({"Gym": {"decision": "kept"}})],
        }

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_streak
            assert get_streak("Gym") == 2

    def test_no_logs_returns_zero(self):
        with patch("utils.streak_tracker._load_logs_for_date", return_value=[]), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_streak
            assert get_streak("Reading") == 0

    def test_streak_capped_at_seven(self):
        # Even if all 7 days are kept, max is 7
        d = _dates_back(7)
        day_logs = {day: [_make_log({"Gym": {"decision": "kept"}})] for day in d}

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_streak
            assert get_streak("Gym") == 7

    def test_any_kept_log_counts_as_kept_day(self):
        # Multiple logs per day — if any says kept, day counts
        d = _dates_back(1)
        day_logs = {
            d[0]: [
                _make_log({"Gym": {"decision": "dropped"}}),
                _make_log({"Gym": {"decision": "kept"}}),
            ]
        }

        def fake_load(client, date_str):
            return day_logs.get(date_str, [])

        with patch("utils.streak_tracker._load_logs_for_date", side_effect=fake_load), \
             patch("utils.streak_tracker._get_s3_client"):
            from utils.streak_tracker import get_streak
            assert get_streak("Gym") == 1
