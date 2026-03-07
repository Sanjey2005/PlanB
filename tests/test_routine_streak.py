"""Tests for agents/routine streak protection logic."""

from unittest.mock import patch, MagicMock

from state import get_initial_state
from agents.routine import routine_agent

_ROUTINE_EVENT = {
    "id": "gym1",
    "summary": "Gym",
    "start": "2026-03-05T07:00:00+05:30",
    "end": "2026-03-05T08:00:00+05:30",
    "attendees": [],
    "extendedProperties": {},
}


def _mock_llm(decision="kept", reason="No disruption today."):
    mock = MagicMock()
    mock.invoke.return_value.content = f"{decision}\n{reason}"
    return mock


def _run(task_scores, drop_count, llm_decision="kept", events=None):
    if events is None:
        events = [_ROUTINE_EVENT]
    state = get_initial_state()
    state["task_scores"] = task_scores
    state["severity"] = "high"
    state["fatigue_level"] = "none"

    with patch("agents.routine.get_todays_events", return_value=events), \
         patch("agents.routine.get_drop_count_last_n_days", return_value=drop_count), \
         patch("agents.routine.ChatGroq", return_value=_mock_llm(llm_decision)):
        return routine_agent(state)


class TestStreakProtectionBoost:
    def test_score_boosted_when_dropped_twice(self):
        # Base score 40, drop_count=2 → boosted to 65
        captured_scores = []

        def fake_ask_groq(llm, summary, score, severity, fatigue_level, note=""):
            captured_scores.append(score)
            return "kept", "streak protection active"

        state = get_initial_state()
        state["task_scores"] = {"gym1": 40}
        state["severity"] = "high"
        state["fatigue_level"] = "none"

        with patch("agents.routine.get_todays_events", return_value=[_ROUTINE_EVENT]), \
             patch("agents.routine.get_drop_count_last_n_days", return_value=2), \
             patch("agents.routine._ask_groq", side_effect=fake_ask_groq), \
             patch("agents.routine.ChatGroq", return_value=MagicMock()):
            routine_agent(state)

        assert captured_scores[0] == 65  # 40 + 25

    def test_score_not_boosted_when_dropped_once(self):
        captured_scores = []

        def fake_ask_groq(llm, summary, score, severity, fatigue_level, note=""):
            captured_scores.append(score)
            return "kept", ""

        state = get_initial_state()
        state["task_scores"] = {"gym1": 40}
        state["severity"] = "low"
        state["fatigue_level"] = "none"

        with patch("agents.routine.get_todays_events", return_value=[_ROUTINE_EVENT]), \
             patch("agents.routine.get_drop_count_last_n_days", return_value=1), \
             patch("agents.routine._ask_groq", side_effect=fake_ask_groq), \
             patch("agents.routine.ChatGroq", return_value=MagicMock()):
            routine_agent(state)

        assert captured_scores[0] == 40  # unchanged

    def test_boost_capped_at_100(self):
        captured_scores = []

        def fake_ask_groq(llm, summary, score, severity, fatigue_level, note=""):
            captured_scores.append(score)
            return "kept", ""

        state = get_initial_state()
        state["task_scores"] = {"gym1": 90}
        state["severity"] = "low"
        state["fatigue_level"] = "none"

        with patch("agents.routine.get_todays_events", return_value=[_ROUTINE_EVENT]), \
             patch("agents.routine.get_drop_count_last_n_days", return_value=3), \
             patch("agents.routine._ask_groq", side_effect=fake_ask_groq), \
             patch("agents.routine.ChatGroq", return_value=MagicMock()):
            routine_agent(state)

        assert captured_scores[0] == 100  # 90+25 capped

    def test_boost_zero_drop_count(self):
        captured_scores = []

        def fake_ask_groq(llm, summary, score, severity, fatigue_level, note=""):
            captured_scores.append(score)
            return "kept", ""

        state = get_initial_state()
        state["task_scores"] = {"gym1": 55}
        state["severity"] = "low"
        state["fatigue_level"] = "none"

        with patch("agents.routine.get_todays_events", return_value=[_ROUTINE_EVENT]), \
             patch("agents.routine.get_drop_count_last_n_days", return_value=0), \
             patch("agents.routine._ask_groq", side_effect=fake_ask_groq), \
             patch("agents.routine.ChatGroq", return_value=MagicMock()):
            routine_agent(state)

        assert captured_scores[0] == 55  # unchanged


class TestStreakProtectedFlag:
    def test_streak_protected_true_when_boosted(self):
        r = _run(task_scores={"gym1": 40}, drop_count=2)
        assert r["routine_decisions"]["Gym"]["streak_protected"] is True

    def test_streak_protected_false_when_not_triggered(self):
        r = _run(task_scores={"gym1": 40}, drop_count=1)
        assert r["routine_decisions"]["Gym"]["streak_protected"] is False

    def test_drop_count_stored_in_decisions(self):
        r = _run(task_scores={"gym1": 40}, drop_count=2)
        assert r["routine_decisions"]["Gym"]["drop_count"] == 2

    def test_drop_count_zero_stored(self):
        r = _run(task_scores={"gym1": 40}, drop_count=0)
        assert r["routine_decisions"]["Gym"]["drop_count"] == 0


class TestRoutineDecisionsSchema:
    def test_all_required_keys_present(self):
        r = _run(task_scores={"gym1": 60}, drop_count=0)
        dec = r["routine_decisions"]["Gym"]
        for key in ("decision", "reason", "event_id", "streak_protected", "drop_count"):
            assert key in dec, f"Missing key: {key}"

    def test_decision_is_valid(self):
        r = _run(task_scores={"gym1": 60}, drop_count=0)
        dec = r["routine_decisions"]["Gym"]["decision"]
        assert dec in ("kept", "compressed", "delayed", "dropped")

    def test_event_id_matches(self):
        r = _run(task_scores={"gym1": 60}, drop_count=0)
        assert r["routine_decisions"]["Gym"]["event_id"] == "gym1"


class TestStreakProtectionNote:
    def test_note_passed_to_groq_when_streak_active(self):
        captured_notes = []

        def fake_ask_groq(llm, summary, score, severity, fatigue_level, note=""):
            captured_notes.append(note)
            return "kept", "note present"

        state = get_initial_state()
        state["task_scores"] = {"gym1": 40}
        state["severity"] = "high"
        state["fatigue_level"] = "none"

        with patch("agents.routine.get_todays_events", return_value=[_ROUTINE_EVENT]), \
             patch("agents.routine.get_drop_count_last_n_days", return_value=2), \
             patch("agents.routine._ask_groq", side_effect=fake_ask_groq), \
             patch("agents.routine.ChatGroq", return_value=MagicMock()):
            routine_agent(state)

        assert "streak protection" in captured_notes[0].lower()

    def test_no_note_when_no_streak(self):
        captured_notes = []

        def fake_ask_groq(llm, summary, score, severity, fatigue_level, note=""):
            captured_notes.append(note)
            return "kept", ""

        state = get_initial_state()
        state["task_scores"] = {"gym1": 60}
        state["severity"] = "low"
        state["fatigue_level"] = "none"

        with patch("agents.routine.get_todays_events", return_value=[_ROUTINE_EVENT]), \
             patch("agents.routine.get_drop_count_last_n_days", return_value=0), \
             patch("agents.routine._ask_groq", side_effect=fake_ask_groq), \
             patch("agents.routine.ChatGroq", return_value=MagicMock()):
            routine_agent(state)

        assert captured_notes[0] == ""


class TestStreakTrackerFailure:
    def test_agent_continues_on_tracker_failure(self):
        state = get_initial_state()
        state["task_scores"] = {"gym1": 60}
        state["severity"] = "low"
        state["fatigue_level"] = "none"

        with patch("agents.routine.get_todays_events", return_value=[_ROUTINE_EVENT]), \
             patch("agents.routine.get_drop_count_last_n_days", side_effect=Exception("S3 down")), \
             patch("agents.routine.ChatGroq", return_value=_mock_llm()):
            result = routine_agent(state)

        assert "Gym" in result["routine_decisions"]
        # streak_protected should default to False on failure
        assert result["routine_decisions"]["Gym"]["streak_protected"] is False
