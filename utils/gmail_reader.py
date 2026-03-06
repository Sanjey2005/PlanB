import os
import base64
import json

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from langchain_google_genai import ChatGoogleGenerativeAI

from config import settings

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]
TOKEN_PATH = "token.json"


def get_gmail_service():
    """Authenticate and return the Gmail service object."""
    creds = None
    try:
        if os.path.exists(TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    settings.GOOGLE_CREDENTIALS_PATH, SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(TOKEN_PATH, "w") as token_file:
                token_file.write(creds.to_json())
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        print(f"Error authenticating Gmail: {e}")
        raise


def _extract_plain_text_body(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

    # Recurse into multipart parts
    for part in payload.get("parts", []):
        text = _extract_plain_text_body(part)
        if text:
            return text

    return ""


def get_recent_emails(max_results: int = 5) -> list:
    """Return the most recent N unread emails from Gmail inbox.

    Each email dict contains: id, subject, sender, body, timestamp.
    """
    try:
        service = get_gmail_service()
        response = service.users().messages().list(
            userId="me",
            q="is:unread in:inbox",
            maxResults=max_results,
        ).execute()

        messages = response.get("messages", [])
        emails = []

        for msg in messages:
            msg_id = msg["id"]
            full_msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full",
            ).execute()

            headers = full_msg.get("payload", {}).get("headers", [])
            header_map = {h["name"].lower(): h["value"] for h in headers}

            subject = header_map.get("subject", "(No Subject)")
            sender = header_map.get("from", "(Unknown Sender)")
            timestamp = full_msg.get("internalDate", "0")

            body = _extract_plain_text_body(full_msg.get("payload", {}))

            emails.append({
                "id": msg_id,
                "subject": subject,
                "sender": sender,
                "body": body,
                "timestamp": timestamp,
            })

        return emails
    except Exception as e:
        print(f"Error fetching recent emails: {e}")
        return []


def understand_email_with_gemini(email_body: str) -> dict:
    """Use Gemini gemini-1.5-flash to determine if an email is a scheduling disruption.

    Returns a dict with: is_disruption, disruption_type, summary, hours_impacted, urgency.
    """
    safe_defaults = {
        "is_disruption": False,
        "disruption_type": "none",
        "summary": "Unable to analyze email.",
        "hours_impacted": 0.0,
        "urgency": "low",
    }

    try:
        llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GEMINI_API_KEY,
        )

        prompt = (
            "Analyze this email and determine if it represents a scheduling disruption.\n"
            "Return ONLY valid JSON with these fields:\n"
            "is_disruption: bool,\n"
            "disruption_type: str (travel/health/calendar/work/external/none),\n"
            "summary: str (one sentence),\n"
            "hours_impacted: float (estimated hours lost, 0 if not a disruption),\n"
            "urgency: str (low/medium/high)\n\n"
            f"Email:\n{email_body}"
        )

        response = llm.invoke(prompt)
        content = response.content.strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        return json.loads(content)

    except json.JSONDecodeError as e:
        print(f"Gemini returned invalid JSON: {e}")
        return safe_defaults
    except Exception as e:
        print(f"Error calling Gemini for email analysis: {e}")
        return safe_defaults
