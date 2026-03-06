"""Tests for graph.py — compilation, node presence, and routing functions."""

import graph
from graph import route_after_orchestrator, route_after_scheduler


class TestGraphCompilation:
    def test_app_is_compiled(self):
        assert graph.app is not None

    def test_all_nodes_present(self):
        nodes = set(graph.app.nodes)
        required = {
            "monitor", "context", "resilience", "orchestrator", "priority",
            "replan", "routine", "scheduler", "negotiate", "comms",
            "predictive_risk", "crisis",
        }
        assert required.issubset(nodes)

    def test_crisis_node_present(self):
        assert "crisis" in graph.app.nodes


class TestRouteAfterOrchestrator:
    def test_crisis_route(self):
        assert route_after_orchestrator({"agents_to_fire": ["crisis", "comms"]}) == "crisis"

    def test_replan_route(self):
        assert route_after_orchestrator({"agents_to_fire": ["replan", "routine", "scheduler", "comms"]}) == "replan"

    def test_predictive_risk_route(self):
        assert route_after_orchestrator({"agents_to_fire": ["predictive_risk", "comms"]}) == "predictive_risk"

    def test_comms_fallback(self):
        assert route_after_orchestrator({"agents_to_fire": ["comms"]}) == "comms"

    def test_empty_agents_falls_back_to_comms(self):
        assert route_after_orchestrator({"agents_to_fire": []}) == "comms"

    def test_none_agents_falls_back_to_comms(self):
        assert route_after_orchestrator({"agents_to_fire": None}) == "comms"

    def test_crisis_beats_replan(self):
        # If somehow both appear, crisis takes priority
        assert route_after_orchestrator({"agents_to_fire": ["crisis", "replan", "comms"]}) == "crisis"

    def test_crisis_beats_predictive_risk(self):
        assert route_after_orchestrator({"agents_to_fire": ["crisis", "predictive_risk", "comms"]}) == "crisis"


class TestRouteAfterScheduler:
    def test_negotiate_route(self):
        assert route_after_scheduler({"agents_to_fire": ["replan", "negotiate", "comms"]}) == "negotiate"

    def test_comms_when_no_negotiate(self):
        assert route_after_scheduler({"agents_to_fire": ["replan", "comms"]}) == "comms"

    def test_none_agents_falls_back_to_comms(self):
        assert route_after_scheduler({"agents_to_fire": None}) == "comms"
