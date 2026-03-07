"""Tests for agents/crisis — crisis agent logic with mocked external calls."""

from unittest.mock import patch, MagicMock, call

from state import get_initial_state
from agents.crisis import crisis_agent

_EVENTS = [
    {
        "id": "e1", "summary": "Gym",
        "start": "2026-03-05T07:00:00+05:30", "end": "2026-03-05T08:00:00+05:30",
        "attendees": [],
    },
    {
        "id": "e2", "summary": "Client Call",
        "start": "2026-03-05T10:00:00+05:30", "end": "2026-03-05T11:00:00+05:30",
        "attendees": ["client@external.com"],
    },
    {
        "id": "e3", "summary": "Deep Work",
        "start": "2026-03-05T11:00:00+05:30", "end": "2026-03-05T13:00:00+05:30",
        "attendees": [],
    },
]

_DND_EVENT = {"id": "dnd1", "start": "2026-03-05T14:00:00+05:30", "end": "2026-03-05T17:00:00+05:30"}


def _run(task_scores: dict, events=None, dnd_event=None, ses_email="me@planb.ai", region="ap-south-1"):
    if events is None:
        events = _EVENTS
    if dnd_event is None:
        dnd_event = _DND_EVENT

    state = get_initial_state()
    state["task_scores"] = task_scores

    with patch("agents.crisis.get_todays_events", return_value=events), \
         patch("agents.crisis.create_event", return_value=dnd_event), \
         patch("agents.crisis.SES_FROM_EMAIL", ses_email), \
         patch("agents.crisis.AWS_REGION", region), \
         patch("agents.crisis.boto3.client", return_value=MagicMock()):
        return crisis_agent(state)


class TestLowPriorityDropping:
    def test_events_below_threshold_dropped(self):
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        names = {t["task_name"] for t in r["proposed_schedule"]}
        assert "Gym" not in names        # score 30 < 50
        assert "Deep Work" not in names  # score 45 < 50

    def test_events_above_threshold_kept(self):
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        names = {t["task_name"] for t in r["proposed_schedule"]}
        assert "Client Call" in names   # score 80 >= 50

    def test_kept_events_have_action_keep(self):
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        for task in r["proposed_schedule"]:
            assert task["action"] == "keep"

    def test_threshold_boundary_exact_50_is_kept(self):
        r = _run({"e1": 50, "e2": 50, "e3": 50})
        assert len(r["proposed_schedule"]) == 3

    def test_threshold_boundary_49_is_dropped(self):
        r = _run({"e1": 49, "e2": 49, "e3": 49})
        assert len(r["proposed_schedule"]) == 0

    def test_unscored_events_are_kept(self):
        # Events with no entry in task_scores have score=None → kept
        r = _run({})
        assert len(r["proposed_schedule"]) == len(_EVENTS)

    def test_all_events_kept_when_all_high_score(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        assert len(r["proposed_schedule"]) == 3


class TestCrisisActions:
    def test_crisis_actions_is_list(self):
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        assert isinstance(r["crisis_actions"], list)

    def test_dropped_actions_recorded(self):
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        dropped = [a for a in r["crisis_actions"] if a["action"] == "dropped"]
        assert len(dropped) == 2

    def test_dropped_action_has_task_name(self):
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        dropped = [a for a in r["crisis_actions"] if a["action"] == "dropped"]
        names = {a["task_name"] for a in dropped}
        assert names == {"Gym", "Deep Work"}

    def test_dropped_action_has_reason(self):
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        dropped = [a for a in r["crisis_actions"] if a["action"] == "dropped"]
        for a in dropped:
            assert "reason" in a
            assert len(a["reason"]) > 0

    def test_calendar_block_action_recorded(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        cal = [a for a in r["crisis_actions"] if a["action"] == "calendar_block_created"]
        assert len(cal) == 1

    def test_calendar_block_has_start_and_end(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        cal = next(a for a in r["crisis_actions"] if a["action"] == "calendar_block_created")
        assert cal["start"] is not None
        assert cal["end"] is not None


class TestDNDCalendarBlock:
    def test_create_event_called_once(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 60, "e2": 80, "e3": 70}
        mock_create = MagicMock(return_value=_DND_EVENT)

        with patch("agents.crisis.get_todays_events", return_value=_EVENTS), \
             patch("agents.crisis.create_event", mock_create), \
             patch("agents.crisis.SES_FROM_EMAIL", "me@planb.ai"), \
             patch("agents.crisis.AWS_REGION", "ap-south-1"):
            crisis_agent(state)

        mock_create.assert_called_once()

    def test_create_event_summary_is_dnd(self):
        from agents.crisis import DND_SUMMARY
        state = get_initial_state()
        state["task_scores"] = {}
        mock_create = MagicMock(return_value=_DND_EVENT)

        with patch("agents.crisis.get_todays_events", return_value=[]), \
             patch("agents.crisis.create_event", mock_create), \
             patch("agents.crisis.SES_FROM_EMAIL", "me@planb.ai"), \
             patch("agents.crisis.AWS_REGION", "ap-south-1"):
            crisis_agent(state)

        args, kwargs = mock_create.call_args
        assert kwargs.get("summary") == DND_SUMMARY or args[0] == DND_SUMMARY

    def test_failed_create_event_not_added_to_actions(self):
        r = _run({"e1": 60}, dnd_event={})  # no "id" → failure
        cal = [a for a in r["crisis_actions"] if a["action"] == "calendar_block_created"]
        assert len(cal) == 0


class TestSESEmails:
    def test_no_emails_when_no_attendees_on_dropped(self):
        # e1 and e3 dropped (low score) but have no attendees
        r = _run({"e1": 30, "e2": 80, "e3": 45})
        email_actions = [a for a in r["crisis_actions"] if a["action"] == "dnd_email"]
        assert len(email_actions) == 0

    def test_email_sent_for_attendee_of_dropped_event(self):
        # e2 (Client Call, has attendee) is dropped
        state = get_initial_state()
        state["task_scores"] = {"e1": 60, "e2": 30, "e3": 60}
        mock_ses = MagicMock()

        with patch("agents.crisis.get_todays_events", return_value=_EVENTS), \
             patch("agents.crisis.create_event", return_value=_DND_EVENT), \
             patch("agents.crisis.SES_FROM_EMAIL", "me@planb.ai"), \
             patch("agents.crisis.AWS_REGION", "ap-south-1"), \
             patch("agents.crisis.boto3.client", return_value=mock_ses):
            result = crisis_agent(state)

        email_actions = [a for a in result["crisis_actions"] if a["action"] == "dnd_email"]
        assert len(email_actions) == 1
        assert email_actions[0]["to"] == "client@external.com"
        assert email_actions[0]["meeting"] == "Client Call"

    def test_email_status_sent_on_success(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 60, "e2": 30, "e3": 60}
        mock_ses = MagicMock()
        mock_ses.send_email.return_value = {}  # no exception → success

        with patch("agents.crisis.get_todays_events", return_value=_EVENTS), \
             patch("agents.crisis.create_event", return_value=_DND_EVENT), \
             patch("agents.crisis.SES_FROM_EMAIL", "me@planb.ai"), \
             patch("agents.crisis.AWS_REGION", "ap-south-1"), \
             patch("agents.crisis.boto3.client", return_value=mock_ses):
            result = crisis_agent(state)

        email_actions = [a for a in result["crisis_actions"] if a["action"] == "dnd_email"]
        assert email_actions[0]["status"] == "sent"

    def test_email_status_failed_on_ses_error(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 60, "e2": 30, "e3": 60}
        mock_ses = MagicMock()
        mock_ses.send_email.side_effect = Exception("SES error")

        with patch("agents.crisis.get_todays_events", return_value=_EVENTS), \
             patch("agents.crisis.create_event", return_value=_DND_EVENT), \
             patch("agents.crisis.SES_FROM_EMAIL", "me@planb.ai"), \
             patch("agents.crisis.AWS_REGION", "ap-south-1"), \
             patch("agents.crisis.boto3.client", return_value=mock_ses):
            result = crisis_agent(state)

        email_actions = [a for a in result["crisis_actions"] if a["action"] == "dnd_email"]
        assert email_actions[0]["status"] == "failed"

    def test_no_ses_call_when_ses_from_email_missing(self):
        state = get_initial_state()
        state["task_scores"] = {"e1": 60, "e2": 30, "e3": 60}
        mock_boto = MagicMock()

        with patch("agents.crisis.get_todays_events", return_value=_EVENTS), \
             patch("agents.crisis.create_event", return_value=_DND_EVENT), \
             patch("agents.crisis.SES_FROM_EMAIL", None), \
             patch("agents.crisis.AWS_REGION", "ap-south-1"), \
             patch("agents.crisis.boto3.client", mock_boto):
            crisis_agent(state)

        mock_boto.assert_not_called()


class TestStateWriteback:
    def test_proposed_schedule_written_to_state(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        assert r["proposed_schedule"] is not None
        assert isinstance(r["proposed_schedule"], list)

    def test_crisis_actions_written_to_state(self):
        r = _run({"e1": 60, "e2": 80, "e3": 70})
        assert r["crisis_actions"] is not None

    def test_agent_survives_get_todays_events_failure(self):
        state = get_initial_state()
        state["task_scores"] = {}
        with patch("agents.crisis.get_todays_events", side_effect=Exception("calendar down")), \
             patch("agents.crisis.create_event", return_value=_DND_EVENT), \
             patch("agents.crisis.SES_FROM_EMAIL", "me@planb.ai"), \
             patch("agents.crisis.AWS_REGION", "ap-south-1"):
            result = crisis_agent(state)
        assert result is not None
        assert isinstance(result.get("crisis_actions"), list)
