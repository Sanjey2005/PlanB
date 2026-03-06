import json
import os
from datetime import datetime, date

import boto3
from dotenv import load_dotenv

from config.settings import AWS_REGION, S3_BUCKET_NAME

load_dotenv()


def _json_serializer(obj):
    """Convert datetime/date objects to ISO format strings for JSON serialization."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def log_pipeline_run(state: dict, run_id: str) -> str:
    """Serialize PlanB state as JSON and upload to S3 for audit trail."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        s3_key = f"logs/{today}/{run_id}.json"

        payload = json.dumps(state, default=_json_serializer, indent=2)

        client = boto3.client("s3", region_name=AWS_REGION)
        client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=payload.encode("utf-8"),
            ContentType="application/json",
        )

        return s3_key
    except Exception as e:
        print(f"Error logging pipeline run {run_id} to S3: {e}")
        return ""


def get_last_pipeline_run(user_phone: str) -> dict:
    """Return the most recent completed pipeline run log for the given user_phone.

    Lists all objects under logs/, sorts by LastModified descending, and downloads
    each in turn until one is found where pipeline_complete == True and
    user_phone matches (if provided). Returns {} if none found.
    """
    try:
        client = boto3.client("s3", region_name=AWS_REGION)
        paginator = client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=S3_BUCKET_NAME, Prefix="logs/")

        all_objects = []
        for page in pages:
            all_objects.extend(page.get("Contents", []))

        all_objects.sort(key=lambda o: o["LastModified"], reverse=True)

        for obj in all_objects:
            try:
                response = client.get_object(Bucket=S3_BUCKET_NAME, Key=obj["Key"])
                body = response["Body"].read().decode("utf-8")
                log = json.loads(body)
                if log.get("pipeline_complete") is True:
                    if not user_phone or log.get("user_phone") == user_phone:
                        log["_run_id"] = obj["Key"]
                        return log
            except Exception as e:
                print(f"[S3] Error reading log {obj['Key']}: {e}")
                continue

        return {}
    except Exception as e:
        print(f"[S3] Error fetching last pipeline run: {e}")
        return {}


def get_recent_logs(limit: int = 10) -> list:
    """Return the most recent N pipeline log entries from S3."""
    try:
        client = boto3.client("s3", region_name=AWS_REGION)

        paginator = client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=S3_BUCKET_NAME, Prefix="logs/")

        all_objects = []
        for page in pages:
            all_objects.extend(page.get("Contents", []))

        all_objects.sort(key=lambda o: o["LastModified"], reverse=True)
        top_objects = all_objects[:limit]

        logs = []
        for obj in top_objects:
            try:
                response = client.get_object(Bucket=S3_BUCKET_NAME, Key=obj["Key"])
                body = response["Body"].read().decode("utf-8")
                logs.append(json.loads(body))
            except Exception as e:
                print(f"Error downloading log {obj['Key']}: {e}")

        return logs
    except Exception as e:
        print(f"Error fetching recent logs from S3: {e}")
        return []
