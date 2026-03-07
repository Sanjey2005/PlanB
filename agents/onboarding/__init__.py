import os
from urllib.parse import quote

from dotenv import load_dotenv

from state import PlanBState
from utils.user_dna import init_user_dna

load_dotenv()


def onboarding_agent(state: PlanBState) -> PlanBState:
    """Onboarding Agent — runs exactly once for every new WhatsApp user.

    Creates a default User DNA profile in S3 so the user is registered
    immediately. Sets is_new_user=True so comms_agent sends the welcome
    message. After this pipeline run completes, update_user_dna (called
    automatically by run_pipeline) increments total_pipeline_runs to 1,
    ensuring is_new_user returns False on all subsequent messages.

    Reads from state:
        user_phone (str): WhatsApp number of the new user.

    Writes to state:
        is_new_user (bool): True — signals comms to send the welcome message.
        oauth_url (str):    Google Calendar OAuth URL for this user.
    """
    user_phone = state.get("user_phone") or ""
    init_user_dna(user_phone)
    state["is_new_user"] = True
    base_url = os.getenv("API_GATEWAY_URL", "http://localhost:8000")
    state["oauth_url"] = f"{base_url}/auth?phone={quote(user_phone)}"
    return state
