"""
PlanB — FastAPI Server

Entry point for the WhatsApp-based AI scheduling assistant. Receives incoming
WhatsApp webhook messages, verifies the webhook during Meta setup, and triggers
the LangGraph agent pipeline in the background. Also exposes a /scheduled
endpoint for AWS EventBridge to invoke morning briefings and evening reviews.

Deployed to AWS Lambda via the Mangum adapter.
"""

from dotenv import load_dotenv

load_dotenv()

import os

from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from graph import run_pipeline
from utils.whatsapp import parse_incoming, verify_webhook
from state import get_initial_state
import uvicorn

app = FastAPI(
    title="PlanB",
    description="WhatsApp-based AI scheduling assistant",
)

# CORS — allow all origins during development / hackathon judging
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve dashboard static assets (JS, CSS, images if any)
_DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "dashboard")
if os.path.isdir(_DASHBOARD_DIR):
    app.mount("/dashboard-static", StaticFiles(directory=_DASHBOARD_DIR), name="dashboard-static")


@app.get("/webhook")
async def webhook_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """WhatsApp webhook verification endpoint.

    Meta sends a GET request with hub.mode, hub.verify_token, and hub.challenge
    query parameters during webhook setup. Returns the challenge string if the
    token matches, otherwise responds with 403 Forbidden.
    """
    result = verify_webhook(hub_mode, hub_verify_token, hub_challenge)
    if result is not None:
        return PlainTextResponse(result)
    return PlainTextResponse("Forbidden", status_code=403)


def _run_disruption_pipeline(message: dict):
    """Background task: run the agent pipeline for an incoming WhatsApp message."""
    try:
        initial_state = get_initial_state()
        initial_state["disruption_raw"] = message["text"]
        initial_state["disruption_source"] = "user_message"
        initial_state["mode"] = "disruption"
        initial_state["user_phone"] = message["from"]
        run_pipeline(initial_state)
    except Exception as e:
        print(f"[Pipeline] Disruption pipeline error: {e}")


@app.post("/webhook")
async def webhook_receive(request: Request, background_tasks: BackgroundTasks):
    """Main webhook — receives all incoming WhatsApp messages.

    Parses the Meta webhook payload, extracts the user message, and launches the
    full agent pipeline as a background task. Always returns 200 immediately so
    Meta does not retry the delivery.
    """
    try:
        payload = await request.json()
        message = parse_incoming(payload)
        if message:
            background_tasks.add_task(_run_disruption_pipeline, message)
    except Exception as e:
        print(f"[Webhook] Error processing incoming message: {e}")

    return {"status": "ok"}


def _run_scheduled_pipeline(mode: str, phone: str):
    """Background task: run the agent pipeline for a scheduled trigger."""
    try:
        initial_state = get_initial_state()
        initial_state["mode"] = mode
        initial_state["disruption_source"] = "scheduled"
        initial_state["user_phone"] = phone
        initial_state["disruption_raw"] = ""
        run_pipeline(initial_state)
    except Exception as e:
        print(f"[Pipeline] Scheduled pipeline error ({mode}): {e}")


@app.post("/scheduled")
async def scheduled_trigger(request: Request, background_tasks: BackgroundTasks):
    """Triggered by AWS EventBridge for morning briefings and evening reviews.

    Expects JSON body: {"mode": "morning_briefing" | "evening_review", "phone": "..."}
    Runs the pipeline in the background and returns immediately.
    """
    mode = "unknown"
    try:
        body = await request.json()
        mode = body.get("mode", "morning_briefing")
        phone = body.get("phone", "")
        background_tasks.add_task(_run_scheduled_pipeline, mode, phone)
    except Exception as e:
        print(f"[Scheduled] Error processing scheduled trigger: {e}")

    return {"status": "ok", "mode": mode}


@app.post("/demo")
async def demo_run():
    """Run a pre-scripted flight-delay disruption scenario with zero live API dependencies.

    Mocks Google Calendar, WhatsApp, AWS SES, and S3. Runs the full LangGraph pipeline
    against realistic fake calendar data and returns the complete result as JSON.
    No auth required — this endpoint is for hackathon judging only.

    Returns:
        {
            "status": "ok",
            "whatsapp_message": str,
            "decision_log": dict,
            "agents_fired": list,
            "pipeline_duration_ms": int,
        }
    """
    from utils.demo_runner import run_demo_pipeline
    return run_demo_pipeline()


@app.get("/status")
async def status_check():
    """Return the most recent completed pipeline run from S3 audit logs.

    Reads the planb-audit-logs-sanjey77 S3 bucket and returns key fields from
    the last successful run. Returns {"status": "idle"} if no recent run is found.
    Polled by Mission Control every 3 seconds.

    Returns:
        {
            "run_id": str,
            "mode": str,
            "agents": list,
            "decisions": dict,
            "timestamp": str,
        }
        or {"status": "idle"} if no completed run found.
    """
    try:
        from utils.s3_logger import get_last_pipeline_run
        log = get_last_pipeline_run("")
        if not log:
            return {"status": "idle"}

        s3_key = log.get("_run_id", "")
        # Extract UUID from key format: logs/YYYY-MM-DD/uuid.json
        parts = s3_key.replace(".json", "").split("/")
        run_id = parts[-1] if parts else s3_key
        timestamp = parts[-2] if len(parts) >= 2 else ""

        return {
            "run_id": run_id,
            "mode": log.get("mode"),
            "agents": log.get("agents_to_fire"),
            "decisions": {
                "severity": log.get("severity"),
                "context_summary": log.get("context_summary"),
                "decision_reasoning": log.get("decision_reasoning"),
                "confirmed_schedule": log.get("confirmed_schedule"),
                "confidence_scores": log.get("confidence_scores"),
                "routine_decisions": log.get("routine_decisions"),
            },
            "timestamp": timestamp,
        }
    except Exception as e:
        print(f"[Status] Error reading S3 log: {e}")
        return {"status": "idle"}


@app.get("/dashboard")
async def serve_dashboard():
    """Serve the Mission Control React dashboard."""
    path = os.path.join(os.path.dirname(__file__), "dashboard", "index.html")
    return FileResponse(path, media_type="text/html")


@app.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "service": "PlanB"}


# AWS Lambda adapter
handler = Mangum(app)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
