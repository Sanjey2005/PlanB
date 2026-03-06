"""Tests for agents/monitor — keyword detection for user_message source.

No external calls are made in the user_message branch, so no mocking needed.
"""

from state import get_initial_state
from agents.monitor import monitor_agent


def _user_msg(text: str) -> dict:
    s = get_initial_state()
    s["disruption_source"] = "user_message"
    s["disruption_raw"] = text
    return s


class TestCrisisKeywords:
    def test_crisis_mode_phrase(self):
        r = monitor_agent(_user_msg("activate crisis mode please"))
        assert r["mode"] == "crisis"
        assert r["crisis_mode"] is True

    def test_panic(self):
        r = monitor_agent(_user_msg("I'm in full panic"))
        assert r["mode"] == "crisis"
        assert r["crisis_mode"] is True

    def test_emergency(self):
        r = monitor_agent(_user_msg("this is a deadline emergency"))
        assert r["mode"] == "crisis"
        assert r["crisis_mode"] is True

    def test_overwhelmed(self):
        # "overwhelmed" moved to STRESS_KEYWORDS — no longer triggers crisis
        r = monitor_agent(_user_msg("I'm totally overwhelmed"))
        assert r["mode"] == "stress"
        assert r["stress_mode"] is True

    def test_im_sick(self):
        r = monitor_agent(_user_msg("i'm sick and can't work"))
        assert r["mode"] == "crisis"
        assert r["crisis_mode"] is True

    def test_crisis_beats_disruption_keywords(self):
        # "emergency" is also in DISRUPTION_KEYWORDS — crisis should win
        r = monitor_agent(_user_msg("deadline emergency and meeting cancelled"))
        assert r["mode"] == "crisis"

    def test_crisis_mode_is_true_not_truthy(self):
        r = monitor_agent(_user_msg("crisis mode"))
        assert r["crisis_mode"] is True


class TestHabitStatsKeywords:
    def test_my_stats(self):
        r = monitor_agent(_user_msg("my stats"))
        assert r["mode"] == "query"
        assert r["disruption_raw"] == "HABIT_STATS_REQUEST"

    def test_show_my_habits(self):
        r = monitor_agent(_user_msg("show my habits"))
        assert r["mode"] == "query"
        assert r["disruption_raw"] == "HABIT_STATS_REQUEST"

    def test_my_stats_case_insensitive(self):
        r = monitor_agent(_user_msg("MY STATS"))
        assert r["disruption_raw"] == "HABIT_STATS_REQUEST"

    def test_habit_stats_beats_query_keywords(self):
        # "show" is in QUERY_KEYWORDS — habit stats should win
        r = monitor_agent(_user_msg("show my habits today"))
        assert r["disruption_raw"] == "HABIT_STATS_REQUEST"

    def test_habit_stats_does_not_set_crisis_mode(self):
        r = monitor_agent(_user_msg("my stats"))
        assert r.get("crisis_mode") is None


class TestStressKeywords:
    def test_stressed(self):
        r = monitor_agent(_user_msg("i'm stressed today"))
        assert r["mode"] == "stress"
        assert r["stress_mode"] is True

    def test_burned_out(self):
        r = monitor_agent(_user_msg("feeling burned out"))
        assert r["mode"] == "stress"

    def test_burnt_out(self):
        r = monitor_agent(_user_msg("I'm burnt out"))
        assert r["mode"] == "stress"

    def test_anxious(self):
        r = monitor_agent(_user_msg("feeling anxious about everything"))
        assert r["mode"] == "stress"

    def test_too_much(self):
        r = monitor_agent(_user_msg("there's too much on my plate"))
        assert r["mode"] == "stress"

    def test_cant_cope(self):
        r = monitor_agent(_user_msg("can't cope today"))
        assert r["mode"] == "stress"

    def test_exhausted_mentally(self):
        r = monitor_agent(_user_msg("I'm exhausted mentally"))
        assert r["mode"] == "stress"

    def test_stress_beats_crisis_for_overwhelmed(self):
        # "overwhelmed" is now exclusively in STRESS_KEYWORDS
        r = monitor_agent(_user_msg("overwhelmed with work"))
        assert r["mode"] == "stress"
        assert r.get("crisis_mode") is None

    def test_stress_does_not_set_crisis_mode(self):
        r = monitor_agent(_user_msg("i'm stressed"))
        assert r.get("crisis_mode") is None

    def test_stress_mode_is_true_not_truthy(self):
        r = monitor_agent(_user_msg("i'm stressed"))
        assert r["stress_mode"] is True

    def test_stress_checked_before_crisis(self):
        # stress keywords take priority; crisis keywords still work for non-overlapping terms
        r = monitor_agent(_user_msg("panic attack"))
        assert r["mode"] == "crisis"  # "panic" is crisis-only


class TestDisruptionKeywords:
    def test_delayed(self):
        r = monitor_agent(_user_msg("my flight is delayed"))
        assert r["mode"] == "disruption"

    def test_cancelled(self):
        r = monitor_agent(_user_msg("meeting cancelled"))
        assert r["mode"] == "disruption"

    def test_sick_without_apostrophe(self):
        # "sick" in disruption keywords; "i'm sick" in crisis keywords
        # plain "feeling sick" hits disruption
        r = monitor_agent(_user_msg("feeling sick today"))
        assert r["mode"] == "disruption"

    def test_headache(self):
        r = monitor_agent(_user_msg("bad headache"))
        assert r["mode"] == "disruption"


class TestQueryKeywords:
    def test_what(self):
        r = monitor_agent(_user_msg("what is on my schedule?"))
        assert r["mode"] == "query"

    def test_list(self):
        r = monitor_agent(_user_msg("list my tasks"))
        assert r["mode"] == "query"

    def test_when(self):
        # "meeting" is a disruption keyword, so use a message without it
        r = monitor_agent(_user_msg("when does my day start?"))
        assert r["mode"] == "query"


class TestOnDemandFallback:
    def test_unknown_message(self):
        r = monitor_agent(_user_msg("hello there"))
        assert r["mode"] == "on_demand"

    def test_empty_message(self):
        r = monitor_agent(_user_msg(""))
        assert r["mode"] == "on_demand"

    def test_crisis_mode_not_set_for_on_demand(self):
        r = monitor_agent(_user_msg("add gym tomorrow"))
        assert r.get("crisis_mode") is None


class TestUnknownSource:
    def test_unknown_source_passthrough(self):
        s = get_initial_state()
        s["disruption_source"] = "unknown"
        s["mode"] = "disruption"
        r = monitor_agent(s)
        assert r["mode"] == "disruption"  # unchanged
