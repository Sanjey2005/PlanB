"""
Habit Learner — PlanB Scheduling Assistant

Reads historical pipeline logs from S3 to learn which routines the user
consistently protects, and returns score adjustments for the Priority Engine.

Score adjustment rule:
    +5 per user override (routine agent said "drop" but scheduler kept it),
    capped at +30 total per task.

Session caches avoid repeated S3 reads within the same Lambda invocation.
Caches are keyed by (user_phone, task_name) to prevent cross-user leakage.
"""

import json
from datetime import datetime, timedelta

import boto3
from dotenv import load_dotenv

from config.settings import AWS_REGION, S3_BUCKET_NAME

load_dotenv()

# Session caches — keyed by (user_phone, task_name)
_score_cache: dict = {}   # {(user_phone, task_name): score_adjustment}
_stats_cache: dict = {}   # {(user_phone, task_name): {times_kept, times_dropped, user_overrides, total}}

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


def _load_all_logs(days: int, user_phone: str = "") -> list:
    """Load pipeline logs from S3 for the last N days, filtered by user_phone if provided."""
    client = _get_s3_client()
    today = datetime.now().date()
    all_logs = []
    for i in range(1, days + 1):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        logs = _load_logs_for_date(client, date_str)
        if user_phone:
            logs = [log for log in logs if log.get("user_phone") == user_phone]
        all_logs.extend(logs)
    return all_logs


def _compute_stats(logs: list) -> dict:
    """Scan logs and build per-task stats from routine_decisions and confirmed_schedule.

    Returns:
        {task_name: {times_kept, times_dropped, user_overrides, total}}
    """
    stats: dict = {}

    for log in logs:
        routine_decisions = log.get("routine_decisions") or {}
        confirmed_schedule = log.get("confirmed_schedule") or []

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
                if task_name in confirmed_names:
                    stats[task_name]["user_overrides"] += 1

    return stats


def _ensure_cache_loaded(user_phone: str = ""):
    """Populate per-user caches if not already done this session."""
    # Check if we have any cached data for this user
    if any(k[0] == user_phone for k in _stats_cache):
        return
    try:
        logs = _load_all_logs(_DEFAULT_DAYS, user_phone=user_phone)
        raw_stats = _compute_stats(logs)
        for task_name, s in raw_stats.items():
            overrides = s["user_overrides"]
            adjustment = min(_SCORE_CAP, overrides * _SCORE_PER_OVERRIDE)
            _stats_cache[(user_phone, task_name)] = s
            _score_cache[(user_phone, task_name)] = adjustment
    except Exception as e:
        print(f"[HabitLearner] Failed to load S3 logs: {e}")


def get_learned_scores(task_names: list, user_phone: str = "") -> dict:
    """Return {task_name: score_adjustment} for the given list of task names.

    Adjustments are based on user override history from the last 30 days.
    Results are cached per user in memory for the session.
    """
    _ensure_cache_loaded(user_phone=user_phone)
    return {name: _score_cache.get((user_phone, name), 0) for name in task_names}


def get_day_of_week_patterns(user_phone: str = "") -> dict:
    """Detect day-of-week skip/strong patterns from S3 logs.

    Returns: {task_name: {"skip_days": ["Friday"], "strong_days": ["Monday"],
              "insight": "You skip gym 4 out of 5 Fridays"}}
    """
    try:
        logs = _load_all_logs(_DEFAULT_DAYS, user_phone=user_phone)
    except Exception as e:
        print(f"[HabitLearner] Failed to load logs for day-of-week patterns: {e}")
        return {}

    # {task_name: {day_name: {"kept": count, "dropped": count}}}
    day_stats: dict = {}
    DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for log in logs:
        routine_decisions = log.get("routine_decisions") or {}
        if not routine_decisions:
            continue

        # Determine the day of week from the log's current_time or fall back
        current_time = log.get("current_time", "")
        try:
            log_dt = datetime.fromisoformat(current_time)
            day_name = DAY_NAMES[log_dt.weekday()]
        except (ValueError, TypeError):
            continue

        for task_name, data in routine_decisions.items():
            if not task_name:
                continue
            decision = (data.get("decision") or "").lower()
            if decision not in ("kept", "dropped"):
                continue

            if task_name not in day_stats:
                day_stats[task_name] = {}
            if day_name not in day_stats[task_name]:
                day_stats[task_name][day_name] = {"kept": 0, "dropped": 0}

            day_stats[task_name][day_name][decision] += 1

    # Analyze for patterns (min 3 data points, >60% skip = skip_day, >80% kept = strong_day)
    MIN_DATA_POINTS = 3
    SKIP_THRESHOLD = 0.6
    STRONG_THRESHOLD = 0.8

    patterns = {}
    for task_name, days in day_stats.items():
        skip_days = []
        strong_days = []
        insights = []

        for day_name, counts in days.items():
            total = counts["kept"] + counts["dropped"]
            if total < MIN_DATA_POINTS:
                continue

            drop_rate = counts["dropped"] / total
            keep_rate = counts["kept"] / total

            if drop_rate >= SKIP_THRESHOLD:
                skip_days.append(day_name)
                insights.append(f"You skip {task_name} {counts['dropped']} out of {total} {day_name}s")
            elif keep_rate >= STRONG_THRESHOLD:
                strong_days.append(day_name)

        if skip_days or strong_days:
            patterns[task_name] = {
                "skip_days": skip_days,
                "strong_days": strong_days,
                "insight": "; ".join(insights) if insights else "",
            }

    return patterns


def get_all_habit_stats(user_phone: str = "") -> dict:
    """Return full habit stats for all tasks found in the last 30 days of logs.

    Used by the Comms Agent to respond to HABIT_STATS_REQUEST.

    Returns:
        {task_name: {times_kept, times_dropped, user_overrides, total, score_boost}}
    """
    _ensure_cache_loaded(user_phone=user_phone)
    result = {}
    for (phone, task_name), s in _stats_cache.items():
        if phone == user_phone:
            result[task_name] = {
                **s,
                "score_boost": _score_cache.get((user_phone, task_name), 0),
            }
    return result
