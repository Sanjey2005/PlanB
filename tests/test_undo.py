"""Tests for the Undo feature: s3_logger, monitor, undo agent, comms, orchestrator, graph."""

from unittest.mock import patch, MagicMock
import json

from state import get_initial_state
from agents.comms import _build_undo_message
from agents.monitor import monitor_agent
from agents.undo import undo_agent, _compute_old_end
from utils.s3_logger import get_last_pipeline_run


# ── _compute_old_end ──────────────────────────────────────────────────────────

class TestComputeOldEnd:
    def test_preserves_duration_from_new_times(self):
        # new event is 1 hour → old event should also be 1 hour
        old_start = "2026-03-05T09:00:00+05:30"
        new_start = "2026-03-05T14:00:00+05:30"
        new_end   = "2026-03-05T15:00:00+05:30"
        result = _compute_old_end(old_start, new_start, new_end)
        assert "10:00:00" in result

    def test_preserves_30min_duration(self):
        old_start = "2026-03-05T09:00:00+05:30"
        new_start = "2026-03-05T14:00:00+05:30"
        new_end   = "2026-03-05T14:30:00+05:30"
        result = _compute_old_end(old_start, new_start, new_end)
        assert "09:30:00" in result

    def test_fallback_to_one_hour_on_bad_times(self):
        result = _compute_old_end("not-a-date", "bad", "bad")
        assert result == "not-a-date"

    def test_ist_offset_preserved(self):
        old_start = "2026-03-05T09:00:00+05:30"
        result = _compute_old_end(old_start, "2026-03-05T14:00:00+05:30", "2026-03-05T15:00:00+05:30")
        assert "+05:30" in result


# ── get_last_pipeline_run ─────────────────────────────────────────────────────

def _make_s3_object(key: str, last_modified, content: dict):
    obj = {"Key": key, "LastModified": last_modified}
    response_body = MagicMock()
    response_body.read.return_value = json.dumps(content).encode("utf-8")
    return obj, {"Body": response_body}


class TestGetLastPipelineRun:
    def _mock_s3(self, objects_and_contents):
        """
        objects_and_contents: list of (key, last_modified, content_dict)
        Returns mock boto3 client.
        """
        mock_client = MagicMock()

        # list_objects_v2 paginator
        page_contents = [{"Key": k, "LastModified": lm} for k, lm, _ in objects_and_contents]
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"Contents": page_contents}]
        mock_client.get_paginator.return_value = mock_paginator

        # get_object returns content per key
        content_map = {k: c for k, _, c in objects_and_contents}

        def fake_get_object(Bucket, Key):
            body = MagicMock()
            body.read.return_value = json.dumps(content_map[Key]).encode("utf-8")
            return {"Body": body}

        mock_client.get_object.side_effect = fake_get_object
        return mock_client

    def test_returns_most_recent_complete_log(self):
        from datetime import datetime
        objects = [
            ("logs/2026-03-05/run1.json", datetime(2026, 3, 5, 8, 0),
             {"pipeline_complete": True, "user_phone": "+1", "mode": "disruption"}),
            ("logs/2026-03-05/run2.json", datetime(2026, 3, 5, 9, 0),
             {"pipeline_complete": True, "user_phone": "+1", "mode": "query"}),
        ]
        mock_client = self._mock_s3(objects)
        with patch("utils.s3_logger.boto3.client", return_value=mock_client):
            result = get_last_pipeline_run("+1")
        # run2 is more recent
        assert result["mode"] == "query"

    def test_skips_incomplete_runs(self):
        from datetime import datetime
        objects = [
            ("logs/2026-03-05/run1.json", datetime(2026, 3, 5, 9, 0),
             {"pipeline_complete": False, "user_phone": "+1"}),
            ("logs/2026-03-05/run2.json", datetime(2026, 3, 5, 8, 0),
             {"pipeline_complete": True, "user_phone": "+1", "mode": "disruption"}),
        ]
        mock_client = self._mock_s3(objects)
        with patch("utils.s3_logger.boto3.client", return_value=mock_client):
            result = get_last_pipeline_run("+1")
        assert result["mode"] == "disruption"

    def test_returns_empty_dict_when_no_complete_logs(self):
        from datetime import datetime
        objects = [
            ("logs/2026-03-05/run1.json", datetime(2026, 3, 5, 9, 0),
             {"pipeline_complete": False}),
        ]
        mock_client = self._mock_s3(objects)
        with patch("utils.s3_logger.boto3.client", return_value=mock_client):
            result = get_last_pipeline_run("+1")
        assert result == {}

    def test_returns_empty_dict_on_s3_exception(self):
        with patch("utils.s3_logger.boto3.client", side_effect=Exception("S3 down")):
            result = get_last_pipeline_run("+1")
        assert result == {}

    def test_injects_run_id_from_s3_key(self):
        from datetime import datetime
        objects = [
            ("logs/2026-03-05/abc123.json", datetime(2026, 3, 5, 9, 0),
             {"pipeline_complete": True, "user_phone": "+1"}),
        ]
        mock_client = self._mock_s3(objects)
        with patch("utils.s3_logger.boto3.client", return_value=mock_client):
            result = get_last_pipeline_run("+1")
        assert result["_run_id"] == "logs/2026-03-05/abc123.json"

    def test_filters_by_user_phone(self):
        from datetime import datetime
        objects = [
            ("logs/2026-03-05/run1.json", datetime(2026, 3, 5, 9, 0),
             {"pipeline_complete": True, "user_phone": "+9999", "mode": "crisis"}),
            ("logs/2026-03-05/run2.json", datetime(2026, 3, 5, 8, 0),
             {"pipeline_complete": True, "user_phone": "+1", "mode": "disruption"}),
        ]
        mock_client = self._mock_s3(objects)
        with patch("utils.s3_logger.boto3.client", return_value=mock_client):
            result = get_last_pipeline_run("+1")
        assert result["mode"] == "disruption"

    def test_empty_user_phone_returns_any_complete_log(self):
        from datetime import datetime
        objects = [
            ("logs/2026-03-05/run1.json", datetime(2026, 3, 5, 9, 0),
             {"pipeline_complete": True, "user_phone": "+9999", "mode": "query"}),
        ]
        mock_client = self._mock_s3(objects)
        with patch("utils.s3_logger.boto3.client", return_value=mock_client):
            result = get_last_pipeline_run("")
        assert result["mode"] == "query"

    def test_skips_corrupted_log_files(self):
        from datetime import datetime
        page_contents = [
            {"Key": "logs/2026-03-05/bad.json", "LastModified": datetime(2026, 3, 5, 10, 0)},
            {"Key": "logs/2026-03-05/good.json", "LastModified": datetime(2026, 3, 5, 9, 0)},
        ]
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"Contents": page_contents}]
        mock_client.get_paginator.return_value = mock_paginator

        call_count = [0]
        def fake_get_object(Bucket, Key):
            call_count[0] += 1
            if "bad" in Key:
                raise Exception("corrupted")
            body = MagicMock()
            body.read.return_value = json.dumps(
                {"pipeline_complete": True, "user_phone": "+1", "mode": "disruption"}
            ).encode("utf-8")
            return {"Body": body}

        mock_client.get_object.side_effect = fake_get_object
        with patch("utils.s3_logger.boto3.client", return_value=mock_client):
            result = get_last_pipeline_run("+1")
        assert result["mode"] == "disruption"


# ── undo_agent ────────────────────────────────────────────────────────────────

_LAST_RUN = {
    "pipeline_complete": True,
    "user_phone": "+1",
    "_run_id": "logs/2026-03-05/abc.json",
    "confirmed_schedule": [
        {
            "task_id": "evt1",
            "task_name": "Deep Work",
            "old_time": "2026-03-05T09:00:00+05:30",
            "new_time": "2026-03-05T14:00:00+05:30",
            "confidence": 90,
        },
        {
            "task_id": "evt2",
            "task_name": "Gym",
            "old_time": "2026-03-05T07:00:00+05:30",
            "new_time": "2026-03-05T19:00:00+05:30",
            "confidence": 80,
        },
    ],
}


_SENTINEL = object()

def _run_undo(last_run=_SENTINEL, update_result=None):
    state = get_initial_state()
    state["user_phone"] = "+1"
    if update_result is None:
        update_result = {"id": "evt1"}
    resolved_run = _LAST_RUN if last_run is _SENTINEL else last_run
    with patch("agents.undo.get_last_pipeline_run", return_value=resolved_run), \
         patch("agents.undo.update_event_time", return_value=update_result):
        return undo_agent(state)


class TestUndoAgent:
    def test_reverts_moveable_tasks(self):
        r = _run_undo()
        assert len(r["undo_result"]["reverted"]) == 2

    def test_reverted_task_names(self):
        r = _run_undo()
        names = {item["task_name"] for item in r["undo_result"]["reverted"]}
        assert "Deep Work" in names
        assert "Gym" in names

    def test_reverted_to_old_time(self):
        r = _run_undo()
        deep_work = next(i for i in r["undo_result"]["reverted"] if i["task_name"] == "Deep Work")
        assert deep_work["reverted_to"] == "2026-03-05T09:00:00+05:30"

    def test_from_run_set(self):
        r = _run_undo()
        assert r["undo_result"]["from_run"] == "logs/2026-03-05/abc.json"

    def test_no_last_run_returns_empty_reverted(self):
        r = _run_undo(last_run={})
        assert r["undo_result"]["reverted"] == []
        assert r["undo_result"]["from_run"] is None

    def test_no_moveable_tasks_returns_empty_reverted(self):
        run = {**_LAST_RUN, "confirmed_schedule": [
            {"task_id": "e1", "task_name": "Gym", "new_time": "19:00"},  # no old_time
        ]}
        r = _run_undo(last_run=run)
        assert r["undo_result"]["reverted"] == []

    def test_skips_task_without_task_id(self):
        run = {**_LAST_RUN, "confirmed_schedule": [
            {"task_name": "Gym", "old_time": "07:00", "new_time": "19:00"},  # no task_id
        ]}
        r = _run_undo(last_run=run)
        assert r["undo_result"]["reverted"] == []

    def test_skips_task_without_old_time(self):
        run = {**_LAST_RUN, "confirmed_schedule": [
            {"task_id": "e1", "task_name": "Gym", "new_time": "19:00"},
        ]}
        r = _run_undo(last_run=run)
        assert r["undo_result"]["reverted"] == []

    def test_handles_update_failure_gracefully(self):
        state = get_initial_state()
        state["user_phone"] = "+1"
        with patch("agents.undo.get_last_pipeline_run", return_value=_LAST_RUN), \
             patch("agents.undo.update_event_time", side_effect=Exception("calendar down")):
            result = undo_agent(state)
        # Should not raise; reverted list may be empty due to failures
        assert result is not None
        assert isinstance(result["undo_result"]["reverted"], list)

    def test_handles_update_returning_empty(self):
        r = _run_undo(update_result={})
        # update returned empty → not counted as reverted
        assert len(r["undo_result"]["reverted"]) == 0

    def test_undo_result_written_to_state(self):
        r = _run_undo()
        assert "undo_result" in r
        assert isinstance(r["undo_result"], dict)

    def test_agent_survives_s3_exception(self):
        state = get_initial_state()
        with patch("agents.undo.get_last_pipeline_run", side_effect=Exception("S3 down")):
            result = undo_agent(state)
        assert result is not None
        assert result.get("undo_result") is not None


# ── _build_undo_message ───────────────────────────────────────────────────────

class TestBuildUndoMessage:
    def test_nothing_to_undo_when_no_result(self):
        msg = _build_undo_message({})
        assert "Nothing to undo" in msg

    def test_nothing_to_undo_when_empty_reverted(self):
        state = {"undo_result": {"reverted": [], "from_run": None}}
        msg = _build_undo_message(state)
        assert "Nothing to undo" in msg

    def test_done_opener_when_reverted(self):
        state = {"undo_result": {"reverted": [
            {"task_name": "Deep Work", "reverted_to": "2026-03-05T09:00:00+05:30"},
        ]}}
        msg = _build_undo_message(state)
        assert "Done." in msg

    def test_reverted_tasks_listed(self):
        state = {"undo_result": {"reverted": [
            {"task_name": "Deep Work", "reverted_to": "2026-03-05T09:00:00+05:30"},
            {"task_name": "Gym", "reverted_to": "2026-03-05T07:00:00+05:30"},
        ]}}
        msg = _build_undo_message(state)
        assert "Deep Work" in msg
        assert "Gym" in msg

    def test_original_time_shown(self):
        state = {"undo_result": {"reverted": [
            {"task_name": "Deep Work", "reverted_to": "2026-03-05T09:00:00+05:30"},
        ]}}
        msg = _build_undo_message(state)
        assert "2026-03-05T09:00:00+05:30" in msg

    def test_calendar_restored_closer(self):
        state = {"undo_result": {"reverted": [
            {"task_name": "Gym", "reverted_to": "07:00"},
        ]}}
        msg = _build_undo_message(state)
        assert "Your calendar is back to how it was." in msg

    def test_none_undo_result(self):
        state = {"undo_result": None}
        msg = _build_undo_message(state)
        assert "Nothing to undo" in msg

    def test_no_closer_when_nothing_reverted(self):
        msg = _build_undo_message({})
        assert "Your calendar is back" not in msg


# ── Monitor UNDO_KEYWORDS ─────────────────────────────────────────────────────

def _user_msg(text: str) -> dict:
    s = get_initial_state()
    s["disruption_source"] = "user_message"
    s["disruption_raw"] = text
    return s


class TestMonitorUndoKeywords:
    def test_undo(self):
        r = monitor_agent(_user_msg("undo"))
        assert r["mode"] == "undo"
        assert r["disruption_raw"] == "UNDO_REQUEST"

    def test_revert(self):
        r = monitor_agent(_user_msg("revert"))
        assert r["mode"] == "undo"

    def test_undo_that(self):
        r = monitor_agent(_user_msg("undo that please"))
        assert r["mode"] == "undo"

    def test_put_it_back(self):
        r = monitor_agent(_user_msg("put it back"))
        assert r["mode"] == "undo"

    def test_reverse_that(self):
        r = monitor_agent(_user_msg("reverse that"))
        assert r["mode"] == "undo"

    def test_undo_case_insensitive(self):
        r = monitor_agent(_user_msg("UNDO"))
        assert r["mode"] == "undo"

    def test_undo_does_not_set_crisis_mode(self):
        r = monitor_agent(_user_msg("undo"))
        assert r.get("crisis_mode") is None

    def test_undo_does_not_set_stress_mode(self):
        r = monitor_agent(_user_msg("undo"))
        assert r.get("stress_mode") is None

    def test_disruption_raw_set_to_undo_request(self):
        r = monitor_agent(_user_msg("revert"))
        assert r["disruption_raw"] == "UNDO_REQUEST"

    def test_non_undo_message_unaffected(self):
        r = monitor_agent(_user_msg("hello there"))
        assert r["mode"] == "on_demand"
        assert r.get("disruption_raw") != "UNDO_REQUEST"


# ── Orchestrator undo routing ─────────────────────────────────────────────────

class TestOrchestratorUndoRouting:
    def test_undo_mode_agents_to_fire(self):
        from agents.orchestrator import orchestrator_agent
        state = get_initial_state()
        state["mode"] = "undo"
        state["disruption_raw"] = "UNDO_REQUEST"
        result = orchestrator_agent(state)
        assert result["agents_to_fire"] == ["undo", "comms"]

    def test_undo_does_not_include_replan(self):
        from agents.orchestrator import orchestrator_agent
        state = get_initial_state()
        state["mode"] = "undo"
        state["disruption_raw"] = "UNDO_REQUEST"
        result = orchestrator_agent(state)
        assert "replan" not in result["agents_to_fire"]

    def test_undo_does_not_include_crisis(self):
        from agents.orchestrator import orchestrator_agent
        state = get_initial_state()
        state["mode"] = "undo"
        state["disruption_raw"] = "UNDO_REQUEST"
        result = orchestrator_agent(state)
        assert "crisis" not in result["agents_to_fire"]


# ── Graph undo node ───────────────────────────────────────────────────────────

class TestGraphUndoNode:
    def test_undo_node_in_graph(self):
        from graph import app
        assert "undo" in app.nodes

    def test_route_undo(self):
        from graph import route_after_orchestrator
        assert route_after_orchestrator({"agents_to_fire": ["undo", "comms"]}) == "undo"

    def test_crisis_beats_undo_in_routing(self):
        from graph import route_after_orchestrator
        assert route_after_orchestrator({"agents_to_fire": ["crisis", "undo", "comms"]}) == "crisis"

    def test_undo_beats_stress_in_routing(self):
        from graph import route_after_orchestrator
        assert route_after_orchestrator({"agents_to_fire": ["undo", "stress", "comms"]}) == "undo"


# ── State undo_result field ───────────────────────────────────────────────────

class TestStateUndoField:
    def test_undo_result_in_initial_state(self):
        s = get_initial_state()
        assert "undo_result" in s
        assert s["undo_result"] is None

    def test_undo_result_in_annotations(self):
        from state import PlanBState
        assert "undo_result" in PlanBState.__annotations__
