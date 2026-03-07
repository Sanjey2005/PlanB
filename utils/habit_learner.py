"""
Habit Learner — PlanB Scheduling Assistant

Reads historical pipeline logs from S3 to learn which routines the user
consistently protects, and returns score adjustments for the Priority Engine.

Score adjustment rule:
    +5 per user override (routine agent said "drop" but scheduler kept it),
    capped at +30 total per task.

Session caches avoid repeated S3 reads within the same Lambda invocation.
"""

import json
from datetime import datetime, timedelta

import boto3
from dotenv import load_dotenv

from config.settings import AWS_REGION, S3_BUCKET_NAME

load_dotenv()

# Session caches — keyed by task name (event summary)
_score_cache: dict = {}   # {task_name: score_adjustment}
_stats_cache: dict = {}   # {task_name: {times_kept, times_dropped, user_overrides, total}}

_SCORE_CAP = 30
_SCORE_PER_OVERRIDE = 5
_DEFAULT_DAYS = 30


def _get_s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


def _load_logs_for_date(client, date_str: str) -> list:
    """Fetch and parse all pipeline log JSONs under logs/YYYY-MM-DD/ on S3."""
    prefix = f"logs/{date_str}/"
    logs = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=S3_BUCKET_NAME, Prefix=prefix)
        for page in pages:
            for obj in page.get("Contents", []):
                try:
                    response = client.get_object(Bucket=S3_BUCKET_NAME, Key=obj["Key"])
                    body = response["Body"].read().decode("utf-8")
                    logs.append(json.loads(body))
                except Exception as e:
                    print(f"[HabitLearner] Error reading {obj['Key']}: {e}")
    except Exception as e:
        print(f"[HabitLearner] Error listing logs for {date_str}: {e}")
    return logs


def _load_all_logs(days: int) -> list:
    """Load all pipeline logs from S3 for the last N days (not including today)."""
    client = _get_s3_client()
    today = datetime.now().date()
    all_logs = []
    for i in range(1, days + 1):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        all_logs.extend(_load_logs_for_date(client, date_str))
    return all_logs


def _compute_stats(logs: list) -> dict:
    """Scan logs and build per-task stats from routine_decisions and confirmed_schedule.

    Returns:
        {task_name: {times_kept, times_dropped, user_overrides, total}}

    user_overrides: log entries where routine_decisions said "drop" but the task
    still appears in confirmed_schedule (scheduler/user overrode the drop).
    """
    stats: dict = {}

    for log in logs:
        routine_decisions = log.get("routine_decisions") or {}
        confirmed_schedule = log.get("confirmed_schedule") or []

        # Build set of task names that made it into the confirmed schedule
        confirmed_names = {
            entry.get("task_name", "")
            for entry in confirmed_schedule
            if entry.get("task_name")
        }

        for task_name, data in routine_decisions.items():
            if not task_name:
                continue
            if task_name not in stats:
                stats[task_name] = {
                    "times_kept": 0,
                    "times_dropped": 0,
                    "user_overrides": 0,
                    "total": 0,
                }

            decision = (data.get("decision") or "").lower()
            stats[task_name]["total"] += 1

            if decision == "kept":
                stats[task_name]["times_kept"] += 1
            elif decision == "dropped":
                stats[task_name]["times_dropped"] += 1
                # Override: routine said drop but task still appeared in confirmed schedule
                if task_name in confirmed_names:
                    stats[task_name]["user_overrides"] += 1

    return stats


def _ensure_cache_loaded():
    """Populate _score_cache and _stats_cache if not already done this session."""
    if _stats_cache:
        return  # already loaded
    try:
        logs = _load_all_logs(_DEFAULT_DAYS)
        raw_stats = _compute_stats(logs)
        for task_name, s in raw_stats.items():
            overrides = s["user_overrides"]
            adjustment = min(_SCORE_CAP, overrides * _SCORE_PER_OVERRIDE)
            _stats_cache[task_name] = s
            _score_cache[task_name] = adjustment
    except Exception as e:
        print(f"[HabitLearner] Failed to load S3 logs: {e}")


def get_learned_scores(task_names: list) -> dict:
    """Return {task_name: score_adjustment} for the given list of task names.

    Adjustments are based on user override history from the last 30 days.
    Results are cached in memory for the session.

    Args:
        task_names: List of event summary strings (calendar event names).

    Returns:
        Dict mapping each task name to an int score adjustment (0 to +30).
        Tasks with no history return 0.
    """
    _ensure_cache_loaded()
    return {name: _score_cache.get(name, 0) for name in task_names}


def get_all_habit_stats() -> dict:
    """Return full habit stats for all tasks found in the last 30 days of logs.

    Used by the Comms Agent to respond to HABIT_STATS_REQUEST.

    Returns:
        {task_name: {times_kept, times_dropped, user_overrides, total, score_boost}}
    """
    _ensure_cache_loaded()
    result = {}
    for task_name, s in _stats_cache.items():
        result[task_name] = {
            **s,
            "score_boost": _score_cache.get(task_name, 0),
        }
    return result
