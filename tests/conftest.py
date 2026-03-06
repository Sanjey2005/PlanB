"""Shared pytest fixtures for PlanB test suite."""

import pytest
import utils.habit_learner as hl


@pytest.fixture(autouse=True)
def clear_habit_learner_cache():
    """Clear habit learner session caches before and after every test."""
    hl._score_cache.clear()
    hl._stats_cache.clear()
    yield
    hl._score_cache.clear()
    hl._stats_cache.clear()
