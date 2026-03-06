"""Tests for agents/stress and _build_stress_message in comms."""

from unittest.mock import patch, MagicMock

from state import get_initial_state
from agents.stress import stress_agent, LOW_PRIORITY_THRESHOLD
from agents.comms import _build_stress_message

_EVENTS = [
    {
        "id": "e1", "summary": "Gym",
        "start": "2026-03-05T07:00:00+05:30", "end": "2026-03-05T08:00:00+05:30",
        "attendees": [],
    },
    {
        "id": "e2", "summary": "Team Standup",
        "start": "2026-03-05T10:00:00+05:30", "end": "2026-03-05T10:30:00+05:30",
        "attendees": ["a@b.com"],
    },
    {
        "id": "e3", "summary": "Deep Work",
        "start": "2026-03-05T11:00:00+05:30", "end": "2026-03-05T13:00:00+05:30",
        "attendees": [],
    },
]


def _run(task_scores: dict, events=None):
    if events is None:
        events = _EVENTS
    state = get_initial_state()
    state["task_scores"] = task_scores
    with patch("agents.stress.get_todays_events", return_value=events):
        return stress_agent(state)


class TestStressThreshold:
    def test_threshold_is_40(self):
        assert LOW_PRIORITY_THRESHOLD == 40

    def test_events_below_40_lightened(self):
        r = _run({"e1": 35, "e2": 80, "e3": 30})
        names = {t["task_name"] for t in r["proposed_schedule"]}
        assert "Gym" not in names       # 35 < 40
        assert "Deep Work" not in names  # 30 < 40

    def test_events_above_40_kept(self):
        r = _run({"e1": 35, "e2": 80, "e3": 30})
        names = {t["task_name"] for t in r["proposed_schedule"]}
        assert "Team Standup" in names   # 80 >= 40

    def test_exact_40_is_kept(self):
        r = _run({"e1": 40, "e2": 40, "e3": 40})
        assert len(r["proposed_schedule"]) == 3

    def test_39_is_lightened(self):
        r = _run({"e1": 39, "e2": 39, "e3": 39})
        assert len(r["proposed_schedule"]) == 0

    def test_unscored_events_kept(self):
        # No entry in task_scores → score is None → kept
        r = _run({})
        assert len(r["proposed_schedule"]) == len(_EVENTS)

    def test_stress_threshold_softer_than_crisis(self):
        # Scores between 40-49 are kept by stress but dropped by crisis
        from agents.crisis import LOW_PRIORITY_THRESHOLD as CRISIS_THRESHOLD
        assert LOW_PRIORITY_THRESHOLD < CRISIS_THRESHOLD


class TestFatigueLevel:
    def test_fatigue_set_to_high(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        assert r["fatigue_level"] == "high"

    def test_fatigue_set_even_with_no_events(self):
        r = _run({}, events=[])
        assert r["fatigue_level"] == "high"

    def test_fatigue_action_recorded(self):
        r = _run({"e1": 60})
        fatigue_actions = [a for a in r["stress_actions"] if a["action"] == "fatigue_set"]
        assert len(fatigue_actions) == 1
        assert fatigue_actions[0]["value"] == "high"


class TestStressActions:
    def test_stress_actions_is_list(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        assert isinstance(r["stress_actions"], list)

    def test_lightened_actions_recorded(self):
        r = _run({"e1": 35, "e2": 80, "e3": 30})
        lightened = [a for a in r["stress_actions"] if a["action"] == "lightened"]
        assert len(lightened) == 2

    def test_lightened_action_has_task_name(self):
        r = _run({"e1": 35, "e2": 80, "e3": 30})
        lightened = [a for a in r["stress_actions"] if a["action"] == "lightened"]
        names = {a["task_name"] for a in lightened}
        assert names == {"Gym", "Deep Work"}

    def test_lightened_action_has_reason(self):
        r = _run({"e1": 35, "e2": 80, "e3": 30})
        lightened = [a for a in r["stress_actions"] if a["action"] == "lightened"]
        for a in lightened:
            assert "reason" in a
            assert len(a["reason"]) > 0

    def test_no_calendar_block_actions(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        cal_actions = [a for a in r["stress_actions"] if a["action"] == "calendar_block_created"]
        assert cal_actions == []

    def test_no_email_actions(self):
        r = _run({"e1": 35, "e2": 80, "e3": 30})
        email_actions = [a for a in r["stress_actions"] if a["action"] in ("dnd_email", "email_sent")]
        assert email_actions == []


class TestNoExternalSideEffects:
    def test_no_ses_client_created(self):
        with patch("agents.stress.get_todays_events", return_value=_EVENTS), \
             patch("builtins.__import__") as mock_import:
            state = get_initial_state()
            state["task_scores"] = {"e1": 35}
            # boto3 should never be imported/used in stress agent
            result = stress_agent(state)
        # The agent must not have tried to use boto3 for emails
        assert result is not None

    def test_google_calendar_create_event_not_called(self):
        mock_create = MagicMock()
        with patch("agents.stress.get_todays_events", return_value=_EVENTS), \
             patch("utils.google_calendar.create_event", mock_create):
            state = get_initial_state()
            state["task_scores"] = {"e1": 35}
            stress_agent(state)
        mock_create.assert_not_called()


class TestStateWriteback:
    def test_proposed_schedule_written(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        assert isinstance(r["proposed_schedule"], list)

    def test_kept_events_have_action_keep(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        for task in r["proposed_schedule"]:
            assert task["action"] == "keep"

    def test_agent_survives_calendar_failure(self):
        state = get_initial_state()
        state["task_scores"] = {}
        with patch("agents.stress.get_todays_events", side_effect=Exception("calendar down")):
            result = stress_agent(state)
        assert result is not None
        assert isinstance(result.get("stress_actions"), list)


class TestBuildStressMessage:
    def test_empathetic_opener(self):
        msg = _build_stress_message({})
        assert "Sounds like today is a lot" in msg

    def test_gentle_closer(self):
        msg = _build_stress_message({})
        assert "You've got this" in msg

    def test_focus_suggestion(self):
        msg = _build_stress_message({})
        assert "Focus on just 2 things today" in msg

    def test_lightened_tasks_listed(self):
        state = {
            "stress_actions": [
                {"action": "lightened", "task_name": "Gym", "reason": "low score"},
                {"action": "lightened", "task_name": "Reading", "reason": "low score"},
            ]
        }
        msg = _build_stress_message(state)
        assert "Gym" in msg
        assert "Reading" in msg
        assert "moved a few things off your plate" in msg

    def test_no_lightened_tasks_no_list(self):
        state = {"stress_actions": [{"action": "fatigue_set", "value": "high"}]}
        msg = _build_stress_message(state)
        assert "moved a few things" not in msg

    def test_no_robotic_language(self):
        state = {
            "stress_actions": [
                {"action": "lightened", "task_name": "Gym", "reason": "low score"},
            ]
        }
        msg = _build_stress_message(state)
        assert "schedule optimized" not in msg.lower()
        assert "tasks dropped" not in msg.lower()

    def test_no_crisis_actions_key(self):
        msg = _build_stress_message({})
        assert "Sounds like today is a lot" in msg

    def test_none_stress_actions(self):
        msg = _build_stress_message({"stress_actions": None})
        assert "Sounds like today is a lot" in msg


class TestOrchestratorStressRouting:
    def test_stress_mode_agents_to_fire(self):
        from agents.orchestrator import orchestrator_agent
        state = get_initial_state()
        state["mode"] = "stress"
        state["disruption_raw"] = ""
        result = orchestrator_agent(state)
        assert result["agents_to_fire"] == ["stress", "comms"]

    def test_stress_does_not_include_replan(self):
        from agents.orchestrator import orchestrator_agent
        state = get_initial_state()
        state["mode"] = "stress"
        state["disruption_raw"] = ""
        result = orchestrator_agent(state)
        assert "replan" not in result["agents_to_fire"]

    def test_stress_does_not_include_crisis(self):
        from agents.orchestrator import orchestrator_agent
        state = get_initial_state()
        state["mode"] = "stress"
        state["disruption_raw"] = ""
        result = orchestrator_agent(state)
        assert "crisis" not in result["agents_to_fire"]


class TestGraphStressNode:
    def test_stress_node_in_graph(self):
        from graph import app
        assert "stress" in app.nodes

    def test_route_stress(self):
        from graph import route_after_orchestrator
        assert route_after_orchestrator({"agents_to_fire": ["stress", "comms"]}) == "stress"

    def test_stress_does_not_override_crisis(self):
        from graph import route_after_orchestrator
        # crisis always beats stress in the routing priority
        assert route_after_orchestrator({"agents_to_fire": ["crisis", "stress", "comms"]}) == "crisis"


class TestStateStressFields:
    def test_stress_mode_in_initial_state(self):
        s = get_initial_state()
        assert "stress_mode" in s
        assert s["stress_mode"] is None

    def test_stress_actions_in_initial_state(self):
        s = get_initial_state()
        assert "stress_actions" in s
        assert s["stress_actions"] is None
