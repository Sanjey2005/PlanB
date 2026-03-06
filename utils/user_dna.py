"""
User DNA Profile System — PlanB

Persists and evolves a per-user preference profile in S3 across pipeline runs.
Each profile captures protected habits, fatigue patterns, streak records, and
override history so agents can personalise decisions over time.

S3 key format: user_dna/{user_phone}.json
"""

import json
from datetime import datetime, date

import boto3
from dotenv import load_dotenv

from config.settings import AWS_REGION, S3_BUCKET_NAME

load_dotenv()

_DNA_PREFIX = "user_dna"

_DEFAULT_DNA: dict = {
    "user_phone": "",
    "protected_habits": [],
    "peak_hours": ["9AM-12PM"],
    "never_reschedule": [],
    "preferred_meeting_window": "2PM-5PM",
    "avg_fatigue_pattern": {},
    "learned_overrides": {},
    "crisis_contacts": [],
    "streak_records": {},
    "total_pipeline_runs": 0,
    "last_updated": "",
}


def _json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def get_user_dna(user_phone: str) -> dict:
    """Read and return the User DNA profile for user_phone from S3.

    If no profile exists (new user) or S3 is unreachable, returns a copy of
    the default DNA template with user_phone populated.

    Args:
        user_phone: WhatsApp phone number or identifier (used as the S3 key).

    Returns:
        dict: User DNA profile, always containing every default field.
    """
    dna = dict(_DEFAULT_DNA)
    dna["user_phone"] = user_phone or ""
    # Deep-copy mutable defaults so callers can't mutate the template
    dna["protected_habits"] = list(_DEFAULT_DNA["protected_habits"])
    dna["peak_hours"] = list(_DEFAULT_DNA["peak_hours"])
    dna["never_reschedule"] = list(_DEFAULT_DNA["never_reschedule"])
    dna["crisis_contacts"] = list(_DEFAULT_DNA["crisis_contacts"])
    dna["avg_fatigue_pattern"] = dict(_DEFAULT_DNA["avg_fatigue_pattern"])
    dna["learned_overrides"] = dict(_DEFAULT_DNA["learned_overrides"])
    dna["streak_records"] = dict(_DEFAULT_DNA["streak_records"])

    if not user_phone:
        return dna

    try:
        client = boto3.client("s3", region_name=AWS_REGION)
        key = f"{_DNA_PREFIX}/{user_phone}.json"
        response = client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        body = response["Body"].read().decode("utf-8")
        stored = json.loads(body)
        # Merge stored on top of defaults so new fields are always present
        dna.update(stored)
    except Exception as e:
        if "NoSuchKey" not in str(e):
            print(f"[UserDNA] Could not read profile for {user_phone}: {e}")
        # NoSuchKey for new users is a normal condition — return defaults silently

    return dna


def update_user_dna(user_phone: str, state: dict) -> None:
    """Update and persist the User DNA profile from a completed pipeline run.

    Reads the existing DNA from S3, applies incremental learned updates from
    state, then writes the result back. Existing values are never erased —
    all updates are additive.

    Args:
        user_phone: WhatsApp phone number or identifier.
        state:      Completed PlanBState dict returned by run_pipeline().
    """
    if not user_phone:
        return

    try:
        dna = get_user_dna(user_phone)

        # ── protected_habits ─────────────────────────────────────────────────
        # Tasks the streak tracker flagged as protected (dropped ≥2× in 3 days)
        # are habits worth preserving. Persist them permanently in the DNA.
        routine_decisions = state.get("routine_decisions") or {}
        for task_name, data in routine_decisions.items():
            if data.get("streak_protected") and task_name not in dna["protected_habits"]:
                dna["protected_habits"].append(task_name)

        # ── learned_overrides ────────────────────────────────────────────────
        # Count how many times each task has been successfully rescheduled.
        # High counts signal the user is comfortable moving that task.
        confirmed_schedule = state.get("confirmed_schedule") or []
        for entry in confirmed_schedule:
            task_name = entry.get("task_name", "")
            if task_name and entry.get("new_time"):
                dna["learned_overrides"][task_name] = (
                    dna["learned_overrides"].get(task_name, 0) + 1
                )

        # ── streak_records ───────────────────────────────────────────────────
        # Keep a persistent view of each habit's streak health across runs.
        for task_name, data in routine_decisions.items():
            drop_count = data.get("drop_count", 0)
            prev = dna["streak_records"].get(
                task_name, {"drop_count": 0, "kept_streak": 0}
            )
            if data.get("decision") == "kept":
                prev["kept_streak"] = prev.get("kept_streak", 0) + 1
            else:
                prev["drop_count"] = max(prev.get("drop_count", 0), drop_count)
                prev["kept_streak"] = 0
            dna["streak_records"][task_name] = prev

        # ── last_fatigue ─────────────────────────────────────────────────────
        fatigue_level = state.get("fatigue_level")
        if fatigue_level:
            dna["avg_fatigue_pattern"]["last_fatigue"] = fatigue_level

        # ── bookkeeping ──────────────────────────────────────────────────────
        dna["total_pipeline_runs"] = dna.get("total_pipeline_runs", 0) + 1
        dna["last_updated"] = datetime.now().isoformat()

        # ── persist to S3 ────────────────────────────────────────────────────
        client = boto3.client("s3", region_name=AWS_REGION)
        key = f"{_DNA_PREFIX}/{user_phone}.json"
        payload = json.dumps(dna, default=_json_serializer, indent=2)
        client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=payload.encode("utf-8"),
            ContentType="application/json",
        )
        print(f"[UserDNA] Saved profile for {user_phone} (run #{dna['total_pipeline_runs']})")

    except Exception as e:
        print(f"[UserDNA] Failed to update profile for {user_phone}: {e}")
