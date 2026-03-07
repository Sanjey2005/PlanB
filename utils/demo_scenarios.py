"""
Pre-built demo scenarios for the /demo/scenario/{id} endpoint.

Each scenario describes an input message (or scheduled trigger) and the
expected pipeline behaviour — useful for judging demos and regression testing.
"""

DEMO_SCENARIOS = [
    {
        "id": "basic_disruption",
        "label": "Meeting ran over",
        "message": "My client call ran over by an hour, I'm running late for everything",
        "source": "user_message",
        "expected_mode": "disruption",
        "expected_agents": ["replan", "routine", "scheduler", "comms"],
    },
    {
        "id": "vague_disruption",
        "label": "Vague — something came up",
        "message": "Something came up this afternoon, I can't stick to my plan today",
        "source": "user_message",
        "expected_mode": "disruption",
        "expected_agents": ["replan", "routine", "scheduler", "comms"],
    },
    {
        "id": "advisory_mode",
        "label": "Advisory — just suggest",
        "message": "Just suggest, don't change anything — my 3pm meeting got cancelled",
        "source": "user_message",
        "expected_mode": "disruption",
        "expected_delegation": "advisory",
    },
    {
        "id": "routine_setup",
        "label": "Add a recurring habit",
        "message": "Add gym every weekday at 6:30pm for 1 hour",
        "source": "user_message",
        "expected_mode": "routine_setup",
    },
    {
        "id": "stress_mode",
        "label": "Overwhelmed",
        "message": "I'm completely overwhelmed today, too many things going on",
        "source": "user_message",
        "expected_mode": "stress",
    },
    {
        "id": "query",
        "label": "Schedule query",
        "message": "What do I have this afternoon?",
        "source": "user_message",
        "expected_mode": "query",
    },
    {
        "id": "morning_briefing",
        "label": "Morning scheduled trigger",
        "source": "scheduled",
        "mode": "morning_briefing",
        "message": "",
        "expected_agents": ["predictive_risk", "comms"],
    },
    {
        "id": "habit_stats",
        "label": "Check habit history",
        "message": "Show my stats",
        "source": "user_message",
        "expected_mode": "query",
    },
]

SCENARIO_INDEX = {s["id"]: s for s in DEMO_SCENARIOS}
