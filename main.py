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

import json as _json

from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import PlainTextResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from google_auth_oauthlib.flow import Flow
from mangum import Mangum

from graph import run_pipeline
from utils.whatsapp import parse_incoming, verify_webhook
from state import get_initial_state
import uvicorn

# In-memory metrics — updated by /demo and /live endpoints
_metrics: dict = {
    "total_runs": 0,
    "latencies": [],        # list[int] — pipeline_duration_ms per run
    "habits_protected": 0,
    "last_run_ms": None,
    "agents_fired_last_run": [],
}

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


def _classify_event_type(event: dict) -> str:
    """Classify a Google Calendar event into a display type based on its summary."""
    summary = (event.get("summary") or "").lower()
    if any(w in summary for w in ["standup", "meeting", "call", "sync", "review", "interview", "1:1"]):
        return "meeting"
    if any(w in summary for w in ["gym", "workout", "exercise", "run", "yoga", "fitness", "walk"]):
        return "health"
    if any(w in summary for w in ["lunch", "dinner", "breakfast", "food", "meal", "eat"]):
        return "meal"
    if any(w in summary for w in ["deep work", "focus", "work block", "study", "coding", "writing"]):
        return "focus"
    return "event"


@app.get("/calendar")
async def get_calendar():
    """Return today's and tomorrow's Google Calendar events.

    Returns:
        {
            "today": [{ id, summary, start, end, type }],
            "tomorrow": [{ id, summary, start, end, type }],
        }
    If Calendar is unavailable, returns empty lists (no error raised to the client).
    """
    try:
        from utils.google_calendar import get_todays_events, get_tomorrow_events
        today_raw = get_todays_events()
        tomorrow_raw = get_tomorrow_events()

        def fmt(events: list) -> list:
            return [
                {
                    "id": e.get("id", ""),
                    "summary": e.get("summary", "(No title)"),
                    "start": e.get("start", ""),
                    "end": e.get("end", ""),
                    "type": _classify_event_type(e),
                }
                for e in events
            ]

        return {"today": fmt(today_raw), "tomorrow": fmt(tomorrow_raw)}
    except Exception as e:
        print(f"[Calendar] Error: {e}")
        return {"today": [], "tomorrow": [], "error": str(e)}


@app.get("/metrics")
async def get_metrics():
    """Return aggregate pipeline metrics.

    Tries S3 audit logs first; falls back to in-memory counters accumulated
    during this server process lifetime.

    Returns:
        {
            "total_runs": int,
            "avg_latency_ms": int | null,
            "habits_protected": int,
            "last_run_ms": int | null,
            "agents_fired_last_run": list[str],
        }
    """
    try:
        from utils.s3_logger import get_recent_logs
        logs = get_recent_logs(limit=20)
        if logs:
            latencies = [l.get("pipeline_duration_ms") for l in logs if l.get("pipeline_duration_ms")]
            habits = sum(
                len([v for v in (l.get("routine_decisions") or {}).values()
                     if isinstance(v, dict) and v.get("streak_protected")])
                for l in logs
            )
            last_log = logs[0]
            return {
                "total_runs": len(logs),
                "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
                "habits_protected": habits,
                "last_run_ms": last_log.get("pipeline_duration_ms"),
                "agents_fired_last_run": last_log.get("agents_to_fire") or [],
            }
    except Exception:
        pass

    lats = _metrics["latencies"]
    return {
        "total_runs": _metrics["total_runs"],
        "avg_latency_ms": int(sum(lats) / len(lats)) if lats else None,
        "habits_protected": _metrics["habits_protected"],
        "last_run_ms": _metrics["last_run_ms"],
        "agents_fired_last_run": _metrics["agents_fired_last_run"],
    }


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
            "agent_latencies": dict,
            "pipeline_duration_ms": int,
        }
    """
    from utils.demo_runner import run_demo_pipeline
    result = run_demo_pipeline()

    # Update in-memory metrics
    if result.get("status") == "ok":
        ms = result.get("pipeline_duration_ms")
        dl = result.get("decision_log") or {}
        habits = len([
            v for v in (dl.get("routine_decisions") or {}).values()
            if isinstance(v, dict) and v.get("streak_protected")
        ])
        _metrics["total_runs"] += 1
        if ms:
            _metrics["latencies"].append(ms)
            _metrics["latencies"] = _metrics["latencies"][-100:]
            _metrics["last_run_ms"] = ms
        _metrics["habits_protected"] += habits
        _metrics["agents_fired_last_run"] = result.get("agents_fired") or []

    return result


@app.post("/live")
async def live_run():
    """Run the real pipeline with a preset disruption: 'My meeting ran over by 1 hour'.

    Uses live Google Calendar, WhatsApp, AWS SES, and S3. Streams the LangGraph graph
    to capture per-agent latencies. USER_PHONE env var must be set.

    Returns:
        {
            "status": "ok",
            "whatsapp_message": str,
            "decision_log": dict,
            "agents_fired": list,
            "agent_latencies": dict,
            "pipeline_duration_ms": int,
        }
    """
    import time
    import uuid
    from graph import app as graph_app
    from utils.user_dna import get_user_dna, update_user_dna
    from utils.s3_logger import log_pipeline_run

    LIVE_DISRUPTION = "My meeting ran over by 1 hour"
    phone = os.getenv("USER_PHONE", "")

    initial_state = get_initial_state()
    initial_state["disruption_raw"] = LIVE_DISRUPTION
    initial_state["disruption_source"] = "user_message"
    initial_state["mode"] = "disruption"
    initial_state["user_phone"] = phone

    try:
        initial_state["user_dna"] = get_user_dna(phone)
    except Exception:
        pass

    run_id = str(uuid.uuid4())
    agents_fired: list = []
    agent_latencies: dict = {}
    final_state: dict = dict(initial_state)
    error_info = None

    start_ms = time.time() * 1000
    prev_ms = start_ms

    try:
        for chunk in graph_app.stream(initial_state):
            now_ms = time.time() * 1000
            for node_name, node_updates in chunk.items():
                agents_fired.append(node_name)
                if isinstance(node_updates, dict):
                    final_state.update(node_updates)
                agent_latencies[node_name] = int(now_ms - prev_ms)
                prev_ms = now_ms
    except Exception as exc:
        error_info = str(exc)
        print(f"[Live] Pipeline error: {exc}")

    duration_ms = int(time.time() * 1000 - start_ms)

    try:
        log_pipeline_run(dict(final_state), run_id)
    except Exception:
        pass

    try:
        update_user_dna(phone, dict(final_state))
    except Exception:
        pass

    # Update in-memory metrics
    habits = len([
        v for v in (final_state.get("routine_decisions") or {}).values()
        if isinstance(v, dict) and v.get("streak_protected")
    ])
    _metrics["total_runs"] += 1
    _metrics["latencies"].append(duration_ms)
    _metrics["latencies"] = _metrics["latencies"][-100:]
    _metrics["last_run_ms"] = duration_ms
    _metrics["habits_protected"] += habits
    _metrics["agents_fired_last_run"] = agents_fired

    decision_log = {
        "disruption": LIVE_DISRUPTION,
        "mode": final_state.get("mode"),
        "severity": final_state.get("severity"),
        "hours_impacted": final_state.get("hours_impacted"),
        "context_summary": final_state.get("context_summary"),
        "agents_selected": agents_fired,
        "delegation_depth": final_state.get("delegation_depth"),
        "decision_reasoning": final_state.get("decision_reasoning"),
        "confirmed_schedule": final_state.get("confirmed_schedule"),
        "confidence_scores": final_state.get("confidence_scores"),
        "routine_decisions": final_state.get("routine_decisions"),
        "emails_sent": final_state.get("emails_sent"),
        "agent_latencies": agent_latencies,
    }

    result = {
        "status": "ok" if not error_info else "error",
        "whatsapp_message": final_state.get("whatsapp_message", ""),
        "decision_log": decision_log,
        "agents_fired": agents_fired,
        "agent_latencies": agent_latencies,
        "pipeline_duration_ms": duration_ms,
    }
    if error_info:
        result["error"] = error_info
    return result


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


_OAUTH_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")


def _oauth_redirect_uri() -> str:
    base = os.getenv("API_GATEWAY_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/auth/callback"


@app.get("/auth")
async def start_auth(phone: str = Query(..., description="WhatsApp phone number e.g. 919876543210")):
    """Initiate Google OAuth flow for a specific WhatsApp user.

    Redirects the user's browser to Google's consent screen.  The phone number
    is passed as the OAuth *state* parameter so it survives the redirect and can
    be retrieved in /auth/callback.
    """
    flow = Flow.from_client_secrets_file(
        _CREDENTIALS_PATH,
        scopes=_OAUTH_SCOPES,
        redirect_uri=_oauth_redirect_uri(),
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=phone,
        prompt="consent",
    )
    return RedirectResponse(url=auth_url)


@app.get("/auth/callback")
async def auth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """Handle Google OAuth callback after the user approves access.

    Exchanges the authorisation code for credentials, saves the token to S3 at
    user_tokens/{phone}.json, and redirects to /auth/success.
    """
    if error:
        return HTMLResponse(
            f"<h1>OAuth failed: {error}</h1><p>Close this tab and try again.</p>",
            status_code=400,
        )
    if not code or not state:
        return HTMLResponse("<h1>Missing parameters</h1>", status_code=400)

    phone = state
    try:
        flow = Flow.from_client_secrets_file(
            _CREDENTIALS_PATH,
            scopes=_OAUTH_SCOPES,
            redirect_uri=_oauth_redirect_uri(),
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        token_data = _json.loads(creds.to_json())
        from utils.google_calendar import _save_user_token_to_s3
        _save_user_token_to_s3(phone, token_data)

        return RedirectResponse(url="/auth/success")
    except Exception as e:
        print(f"[Auth] OAuth callback error for {phone}: {e}")
        return HTMLResponse(f"<h1>OAuth error</h1><p>{e}</p>", status_code=500)


@app.get("/auth/success")
async def auth_success():
    """Success page shown after Google Calendar is connected."""
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>PlanB — Connected</title>
  <style>
    body {
      font-family: system-ui, sans-serif;
      background: #050816;
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
    }
    .card {
      text-align: center;
      padding: 40px 32px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 20px;
      max-width: 380px;
      width: 90%;
    }
    .icon { font-size: 56px; margin-bottom: 16px; }
    h2 { margin: 0 0 10px; font-size: 22px; }
    p  { color: rgba(255,255,255,0.55); font-size: 15px; margin: 0; line-height: 1.6; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h2>Calendar connected!</h2>
    <p>Go back to WhatsApp and say hi to PlanB.</p>
  </div>
</body>
</html>
""")


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
