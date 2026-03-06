"""Tests for state.py — PlanBState fields and get_initial_state()."""

from state import PlanBState, get_initial_state


class TestPlanBStateFields:
    def test_has_crisis_mode(self):
        assert "crisis_mode" in PlanBState.__annotations__

    def test_has_crisis_actions(self):
        assert "crisis_actions" in PlanBState.__annotations__

    def test_crisis_mode_is_optional_bool(self):
        import typing
        ann = PlanBState.__annotations__["crisis_mode"]
        args = getattr(ann, "__args__", ())
        assert type(None) in args

    def test_crisis_actions_is_optional_list(self):
        import typing
        ann = PlanBState.__annotations__["crisis_actions"]
        args = getattr(ann, "__args__", ())
        assert type(None) in args


class TestGetInitialState:
    def test_crisis_mode_present_and_none(self):
        s = get_initial_state()
        assert "crisis_mode" in s
        assert s["crisis_mode"] is None

    def test_crisis_actions_present_and_none(self):
        s = get_initial_state()
        assert "crisis_actions" in s
        assert s["crisis_actions"] is None

    def test_all_values_are_none(self):
        s = get_initial_state()
        for key, val in s.items():
            assert val is None, f"Expected {key!r} to be None, got {val!r}"

    def test_contains_all_expected_fields(self):
        s = get_initial_state()
        expected = {
            "disruption_raw", "disruption_source", "mode",
            "disruption_type", "severity", "hours_impacted", "fatigue_level",
            "context_summary", "cascade_map", "deadline_risks", "cascade_severity",
            "task_scores", "agents_to_fire", "delegation_depth", "schedule_conflict",
            "proposed_schedule", "routine_decisions", "confirmed_schedule",
            "moved_meetings", "confidence_scores", "emails_sent", "predictive_risks",
            "crisis_mode", "crisis_actions", "decision_reasoning",
            "stress_mode", "stress_actions",
            "undo_result",
            "whatsapp_message", "pipeline_complete", "user_phone",
        }
        assert expected == set(s.keys())

    def test_decision_reasoning_present_and_none(self):
        s = get_initial_state()
        assert "decision_reasoning" in s
        assert s["decision_reasoning"] is None

    def test_stress_mode_present_and_none(self):
        s = get_initial_state()
        assert "stress_mode" in s
        assert s["stress_mode"] is None

    def test_stress_actions_present_and_none(self):
        s = get_initial_state()
        assert "stress_actions" in s
        assert s["stress_actions"] is None

    def test_returns_new_dict_each_call(self):
        s1 = get_initial_state()
        s2 = get_initial_state()
        s1["mode"] = "crisis"
        assert s2["mode"] is None
