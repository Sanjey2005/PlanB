import httpx
from dotenv import load_dotenv

from config.settings import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN

load_dotenv()

WHATSAPP_API_URL = (
    f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
)


def send_message(to: str, message: str) -> dict:
    """Send a WhatsApp text message via Meta Cloud API."""
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
    try:
        response = httpx.post(WHATSAPP_API_URL, headers=headers, json=body)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        print(f"WhatsApp API error {e.response.status_code}: {e.response.text}")
        return {}
    except Exception as e:
        print(f"Error sending WhatsApp message to {to}: {e}")
        return {}


def parse_incoming(payload: dict) -> dict | None:
    """Parse a raw Meta webhook payload and return message details."""
    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        return {
            "from": message["from"],
            "text": message["text"]["body"],
            "timestamp": message["timestamp"],
        }
    except (KeyError, IndexError, TypeError):
        return None


def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Verify the WhatsApp webhook during setup."""
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return challenge
    return None
