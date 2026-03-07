"""
Shared LLM utilities for PlanB agents.
"""

import json


def parse_llm_json(content: str):
    """Parse JSON from an LLM response, stripping markdown fences if present."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)
