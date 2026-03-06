"""
Streak Tracker — PlanB Scheduling Assistant

Reads historical pipeline logs from S3 to compute routine habit streaks
and recent drop counts. Used by the Routine Agent for streak protection.

S3 log layout (written by utils/s3_logger.py):
  logs/YYYY-MM-DD/{run_id}.json  →  full PlanBState dict
  state["routine_decisions"]     →  {task_name: {"decision": ..., ...}}
"""

import json
from datetime import datetime, timedelta

import boto3
from dotenv import load_dotenv

from config.settings import AWS_REGION, S3_BUCKET_NAME

load_dotenv()


def _get_s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


def _load_logs_for_date(client, date_str: str) -> list:
    """Fetch and parse all pipeline log JSONs stored under logs/YYYY-MM-DD/ on S3."""
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
                    print(f"[StreakTracker] Error reading {obj['Key']}: {e}")
    except Exception as e:
        print(f"[StreakTracker] Error listing logs for {date_str}: {e}")
    return logs


def get_streak(task_name: str) -> int:
    """Return consecutive days (going back from yesterday) where task_name was kept.

    Scans up to the last 7 days of S3 logs. A day counts as "kept" if at least
    one pipeline run on that day recorded decision == "kept" for task_name.
    Stops counting as soon as a day is missing or has no "kept" entry.

    Returns 0 if no logs found or the streak is immediately broken.
    """
    client = _get_s3_client()
    today = datetime.now().date()
    consecutive = 0

    for i in range(1, 8):  # yesterday → 7 days ago
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        logs = _load_logs_for_date(client, date_str)
        if not logs:
            break

        day_kept = any(
            log.get("routine_decisions", {}).get(task_name, {}).get("decision") == "kept"
            for log in logs
        )
        if day_kept:
            consecutive += 1
        else:
            break

    return consecutive


def get_drop_count_last_n_days(task_name: str, n: int) -> int:
    """Return total number of log entries where task_name had decision == 'dropped'
    across all pipeline runs in the last N days (not including today).

    Each pipeline run is counted individually — if a task was dropped in two
    separate runs on the same day, that counts as 2.
    """
    client = _get_s3_client()
    today = datetime.now().date()
    drop_count = 0

    for i in range(1, n + 1):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        logs = _load_logs_for_date(client, date_str)
        for log in logs:
            decision = log.get("routine_decisions", {}).get(task_name, {}).get("decision")
            if decision == "dropped":
                drop_count += 1

    return drop_count
