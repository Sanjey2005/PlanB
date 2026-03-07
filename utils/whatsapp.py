import time

import httpx
from dotenv import load_dotenv

from config.settings import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN

load_dotenv()

WHATSAPP_API_URL = (
    f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
)


def send_message(to: str, message: str) -> dict:
    """Send a WhatsApp text message via Meta Cloud API with up to 3 retries."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    for attempt in range(3):
        try:
            response = httpx.post(WHATSAPP_API_URL, headers=headers, json=body, timeout=10)
            if response.status_code == 429:
                wait = 2 ** attempt
                print(f"WhatsApp rate-limited (429). Retrying in {wait}s (attempt {attempt + 1}/3)")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            wait = 2 ** attempt
            print(f"WhatsApp request timed out. Retrying in {wait}s (attempt {attempt + 1}/3)")
            time.sleep(wait)
        except httpx.HTTPStatusError as e:
            print(f"WhatsApp API error {e.response.status_code}: {e.response.text}")
            return {}
        except Exception as e:
            print(f"Error sending WhatsApp message to {to}: {e}")
            return {}
    print(f"WhatsApp send_message failed after 3 attempts to {to}")
    return {}


def send_buttons(to: str, body_text: str, buttons: list[dict]) -> dict:
    """Send interactive button message. buttons: [{"id": "approve", "title": "Approve"}] (max 3).

    Falls back to send_message() with text instructions if the interactive API call fails.
    """
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                    for b in buttons[:3]
                ]
            }
        }
    }
    for attempt in range(3):
        try:
            response = httpx.post(WHATSAPP_API_URL, headers=headers, json=body, timeout=10)
            if response.status_code == 429:
                wait = 2 ** attempt
                print(f"WhatsApp rate-limited (429). Retrying in {wait}s (attempt {attempt + 1}/3)")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            wait = 2 ** attempt
            print(f"WhatsApp button request timed out. Retrying in {wait}s (attempt {attempt + 1}/3)")
            time.sleep(wait)
        except httpx.HTTPStatusError as e:
            print(f"WhatsApp button API error {e.response.status_code}: {e.response.text}")
            break
        except Exception as e:
            print(f"Error sending WhatsApp buttons to {to}: {e}")
            break

    # Fallback to plain text with button labels as instructions
    btn_labels = ", ".join(f"'{b['title']}'" for b in buttons[:3])
    fallback = f"{body_text}\n\nReply {btn_labels} to respond."
    return send_message(to, fallback)


def send_list(to: str, body_text: str, button_text: str, sections: list[dict]) -> dict:
    """Send interactive list message. Falls back to send_message() on failure."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text[:1024]},
            "action": {
                "button": button_text[:20],
                "sections": sections[:10],
            }
        }
    }
    for attempt in range(3):
        try:
            response = httpx.post(WHATSAPP_API_URL, headers=headers, json=body, timeout=10)
            if response.status_code == 429:
                wait = 2 ** attempt
                print(f"WhatsApp rate-limited (429). Retrying in {wait}s (attempt {attempt + 1}/3)")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            wait = 2 ** attempt
            print(f"WhatsApp list request timed out. Retrying in {wait}s (attempt {attempt + 1}/3)")
            time.sleep(wait)
        except httpx.HTTPStatusError as e:
            print(f"WhatsApp list API error {e.response.status_code}: {e.response.text}")
            break
        except Exception as e:
            print(f"Error sending WhatsApp list to {to}: {e}")
            break

    # Fallback to plain text
    return send_message(to, body_text)


def parse_incoming(payload: dict) -> dict | None:
    """Parse a raw Meta webhook payload and return message details."""
    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        msg_type = message.get("type", "text")

        if msg_type == "text":
            return {
                "from": message["from"],
                "text": message["text"]["body"],
                "timestamp": message["timestamp"],
            }
        elif msg_type == "interactive":
            interactive = message.get("interactive", {})
            if interactive.get("type") == "button_reply":
                reply = interactive["button_reply"]
                return {
                    "from": message["from"],
                    "text": reply["id"],
                    "timestamp": message["timestamp"],
                }
            elif interactive.get("type") == "list_reply":
                reply = interactive["list_reply"]
                return {
                    "from": message["from"],
                    "text": reply["id"],
                    "timestamp": message["timestamp"],
                }
        # Fallback for other types
        return {
            "from": message["from"],
            "text": message.get("text", {}).get("body", ""),
            "timestamp": message.get("timestamp", ""),
        }
    except (KeyError, IndexError, TypeError):
        return None


def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Verify the WhatsApp webhook during setup."""
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return challenge
    return None
