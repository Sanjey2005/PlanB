"""Tests for agents/orchestrator — routing decisions.

Crisis, query, morning_briefing, evening_review modes don't call Groq,
so those branches need no mocking.
"""

from unittest.mock import patch, MagicMock

from state import get_initial_state
from agents.orchestrator import orchestrator_agent


def _state(**kwargs) -> dict:
    s = get_initial_state()
    s.update(kwargs)
    return s


class TestCrisisMode:
    def test_crisis_agents_to_fire(self):
        r = orchestrator_agent(_state(mode="crisis", disruption_raw=""))
        assert r["agents_to_fire"] == ["crisis", "comms"]

    def test_crisis_sets_delegation_depth(self):
        r = orchestrator_agent(_state(mode="crisis", disruption_raw=""))
        assert r["delegation_depth"] in ("autonomous", "assisted", "advisory")

    def test_crisis_does_not_include_replan(self):
        r = orchestrator_agent(_state(mode="crisis", disruption_raw=""))
        assert "replan" not in r["agents_to_fire"]

    def test_crisis_does_not_include_predictive_risk(self):
        r = orchestrator_agent(_state(mode="crisis", disruption_raw=""))
        assert "predictive_risk" not in r["agents_to_fire"]


class TestQueryMode:
    def test_query_agents_to_fire(self):
        r = orchestrator_agent(_state(mode="query", disruption_raw="what's my schedule"))
        assert r["agents_to_fire"] == ["comms"]


class TestMorningBriefingMode:
    def test_morning_briefing_agents(self):
        r = orchestrator_agent(_state(mode="morning_briefing", disruption_raw=""))
        assert "predictive_risk" in r["agents_to_fire"]
        assert "comms" in r["agents_to_fire"]


class TestEveningReviewMode:
    def test_evening_review_agents(self):
        r = orchestrator_agent(_state(mode="evening_review", disruption_raw=""))
        assert "predictive_risk" in r["agents_to_fire"]
        assert "comms" in r["agents_to_fire"]


class TestDisruptionMode:
    def test_high_cascade_adds_negotiate(self):
        r = orchestrator_agent(_state(
            mode="disruption",
            cascade_severity="high",
            severity="high",
            disruption_type="work",
            disruption_raw="",
        ))
        assert "negotiate" in r["agents_to_fire"]

    def test_low_low_removes_routine(self):
        r = orchestrator_agent(_state(
            mode="disruption",
            cascade_severity="low",
            severity="low",
            disruption_type="work",
            disruption_raw="",
        ))
        assert "routine" not in r["agents_to_fire"]

    def test_health_disruption_keeps_routine(self):
        r = orchestrator_agent(_state(
            mode="disruption",
            cascade_severity="low",
            severity="low",
            disruption_type="health",
            disruption_raw="",
        ))
        assert "routine" in r["agents_to_fire"]


class TestDelegationDepth:
    def test_autonomous_keyword(self):
        r = orchestrator_agent(_state(mode="query", disruption_raw="just do it"))
        assert r["delegation_depth"] == "autonomous"

    def test_advisory_keyword(self):
        r = orchestrator_agent(_state(mode="query", disruption_raw="just suggest options"))
        assert r["delegation_depth"] == "advisory"

    def test_default_is_assisted(self):
        r = orchestrator_agent(_state(mode="query", disruption_raw="what's on today"))
        assert r["delegation_depth"] == "assisted"


class TestErrorRecovery:
    def test_unknown_mode_falls_back_to_comms(self):
        r = orchestrator_agent(_state(mode="nonexistent_mode", disruption_raw=""))
        assert r["agents_to_fire"] == ["comms"]
