import json as _json
import os
from datetime import datetime, timedelta

import boto3
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config.settings import AWS_REGION, GOOGLE_CREDENTIALS_PATH, S3_BUCKET_NAME
from utils.validators import sanitize_s3_key_segment

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PATH = "token.json"
TIMEZONE = "Asia/Kolkata"
WORKING_HOUR_START = 7
WORKING_HOUR_END = 22


def get_calendar_service():
    """Authenticate and return the Google Calendar service object."""
    creds = None
    try:
        if os.path.exists(TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    GOOGLE_CREDENTIALS_PATH, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(TOKEN_PATH, "w") as token_file:
                token_file.write(creds.to_json())
        return build("calendar", "v3", credentials=creds)
    except Exception as e:
        print(f"Error authenticating Google Calendar: {e}")
        raise


def _save_user_token_to_s3(phone: str, token_data: dict) -> None:
    """Persist a refreshed or newly-issued OAuth token to S3 user_tokens/{phone}.json."""
    try:
        client = boto3.client("s3", region_name=AWS_REGION)
        safe_phone = sanitize_s3_key_segment(phone)
        client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=f"user_tokens/{safe_phone}.json",
            Body=_json.dumps(token_data).encode("utf-8"),
            ContentType="application/json",
        )
        print(f"[Calendar] Token saved to S3 for {phone}")
    except Exception as e:
        print(f"[Calendar] Failed to save token for {phone}: {e}")


def get_user_token(phone: str) -> dict | None:
    """Read per-user OAuth token from S3 user_tokens/{phone}.json.

    Returns the parsed token dict, or None if the object doesn't exist or
    the read fails.
    """
    if not phone:
        return None
    try:
        client = boto3.client("s3", region_name=AWS_REGION)
        safe_phone = sanitize_s3_key_segment(phone)
        response = client.get_object(
            Bucket=S3_BUCKET_NAME,
            Key=f"user_tokens/{safe_phone}.json",
        )
        return _json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        return None


def build_service(phone: str = None):
    """Return an authenticated Google Calendar service for a specific user.

    If *phone* is provided and a token exists in S3 (user_tokens/{phone}.json),
    that token is used — refreshing and re-persisting it if expired.  Falls back
    to the shared credentials.json / token.json flow when no per-user token is
    available.
    """
    if phone:
        token_data = get_user_token(phone)
        if token_data:
            try:
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    _save_user_token_to_s3(phone, _json.loads(creds.to_json()))
                return build("calendar", "v3", credentials=creds)
            except Exception as e:
                print(f"[Calendar] S3 token unusable for {phone}: {e}")
    return get_calendar_service()


def _parse_event(event: dict) -> dict:
    """Extract relevant fields from a raw Google Calendar event."""
    attendees_raw = event.get("attendees", [])
    extended = event.get("extendedProperties", {})
    return {
        "id": event.get("id"),
        "summary": event.get("summary", "(No title)"),
        "start": event.get("start", {}).get("dateTime", event.get("start", {}).get("date")),
        "end": event.get("end", {}).get("dateTime", event.get("end", {}).get("date")),
        "attendees": [a.get("email") for a in attendees_raw if a.get("email")],
        "extendedProperties": extended,
    }


def get_todays_events(phone: str = None) -> list:
    """Return all events for today in Asia/Kolkata timezone."""
    try:
        service = build_service(phone)
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        time_min = start_of_day.isoformat() + "+05:30"
        time_max = end_of_day.isoformat() + "+05:30"

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            timeZone=TIMEZONE,
        ).execute()

        return [_parse_event(e) for e in result.get("items", [])]
    except Exception as e:
        print(f"Error fetching today's events: {e}")
        return []


def get_tomorrow_events(phone: str = None) -> list:
    """Return all events for tomorrow in Asia/Kolkata timezone."""
    try:
        service = build_service(phone)
        now = datetime.now()
        start_of_tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_tomorrow = start_of_tomorrow + timedelta(days=1)

        time_min = start_of_tomorrow.isoformat() + "+05:30"
        time_max = end_of_tomorrow.isoformat() + "+05:30"

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            timeZone=TIMEZONE,
        ).execute()

        return [_parse_event(e) for e in result.get("items", [])]
    except Exception as e:
        print(f"Error fetching tomorrow's events: {e}")
        return []


def get_events_range(days: int, phone: str = None) -> list:
    """Return all events for the next N days."""
    try:
        service = build_service(phone)
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_of_day + timedelta(days=days)

        time_min = start_of_day.isoformat() + "+05:30"
        time_max = end_date.isoformat() + "+05:30"

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            timeZone=TIMEZONE,
        ).execute()

        return [_parse_event(e) for e in result.get("items", [])]
    except Exception as e:
        print(f"Error fetching events for next {days} days: {e}")
        return []


def update_event_time(event_id: str, new_start: str, new_end: str, phone: str = None) -> dict:
    """Move an existing event to a new time. Times are ISO 8601 with IST offset."""
    try:
        service = build_service(phone)
        event = service.events().get(
            calendarId="primary", eventId=event_id
        ).execute()

        event["start"] = {"dateTime": new_start, "timeZone": TIMEZONE}
        event["end"] = {"dateTime": new_end, "timeZone": TIMEZONE}

        updated = service.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()

        return _parse_event(updated)
    except Exception as e:
        print(f"Error updating event {event_id}: {e}")
        return {}


def create_event(summary: str, start: str, end: str, metadata: dict = None, phone: str = None) -> dict:
    """Create a new calendar event. Optionally store PlanB metadata in extendedProperties."""
    try:
        service = build_service(phone)
        event_body = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": TIMEZONE},
            "end": {"dateTime": end, "timeZone": TIMEZONE},
        }

        if metadata:
            event_body["extendedProperties"] = {
                "private": {k: str(v) for k, v in metadata.items()}
            }

        created = service.events().insert(
            calendarId="primary", body=event_body
        ).execute()

        return _parse_event(created)
    except Exception as e:
        print(f"Error creating event '{summary}': {e}")
        return {}


def get_free_slots(date_str: str, duration_minutes: int, phone: str = None) -> list:
    """Return available time slots on a given date for a given duration.

    Args:
        date_str: Date in YYYY-MM-DD format.
        duration_minutes: Required slot duration in minutes.

    Returns:
        List of dicts with 'start' and 'end' ISO 8601 strings.
    """
    try:
        service = build_service(phone)
        date = datetime.strptime(date_str, "%Y-%m-%d")
        day_start = date.replace(hour=WORKING_HOUR_START, minute=0, second=0, microsecond=0)
        day_end = date.replace(hour=WORKING_HOUR_END, minute=0, second=0, microsecond=0)

        time_min = day_start.isoformat() + "+05:30"
        time_max = day_end.isoformat() + "+05:30"

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            timeZone=TIMEZONE,
        ).execute()

        # WITH THIS:
        busy_periods = []
        for event in result.get("items", []):
            e_start = event.get("start", {}).get("dateTime")
            e_end = event.get("end", {}).get("dateTime")
            if e_start and e_end:
                import re
                strip_tz = lambda s: re.sub(r"[+-]\d{2}:\d{2}$", "", s.strip())
                busy_periods.append((
                    datetime.fromisoformat(strip_tz(e_start)),
                    datetime.fromisoformat(strip_tz(e_end)),
                ))

        busy_periods.sort(key=lambda x: x[0])

        free_slots = []
        cursor = day_start
        duration = timedelta(minutes=duration_minutes)

        for busy_start, busy_end in busy_periods:
            while cursor + duration <= busy_start:
                slot_end = cursor + duration
                free_slots.append({
                    "start": cursor.isoformat() + "+05:30",
                    "end": slot_end.isoformat() + "+05:30",
                })
                cursor = slot_end
            if busy_end > cursor:
                cursor = busy_end

        while cursor + duration <= day_end:
            slot_end = cursor + duration
            free_slots.append({
                "start": cursor.isoformat() + "+05:30",
                "end": slot_end.isoformat() + "+05:30",
            })
            cursor = slot_end

        return free_slots
    except Exception as e:
        print(f"Error finding free slots for {date_str}: {e}")
        return []
