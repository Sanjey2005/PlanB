# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PlanB is a WhatsApp-based autonomous AI scheduling assistant built for the NeoVerse 26 hackathon. It uses LangGraph for agent orchestration, FastAPI as the web framework, and integrates with WhatsApp (Meta Cloud API), Google Calendar, Gmail, and AWS services.

## Commands
```bash
# Install dependencies
pip install -r requirements.txt

# Run the FastAPI server
uvicorn main:app --reload --port 8000

# Run tests
pytest tests/
pytest tests/test_specific.py -v
```

## Architecture

11 specialized LangGraph agents communicate through a single shared PlanBState TypedDict. Each agent is a node in the LangGraph graph. The Orchestrator decides which agents fire at runtime based on disruption type and severity — the pipeline is never fixed.

Agents fire in this sequence:
Monitor → Context → Resilience → Orchestrator → Priority Engine → Replan → Routine → Scheduler → Negotiate → Comms

Predictive Risk Agent fires separately during morning_briefing and evening_review modes only.

Supporting directories:
- `config/` - Configuration files and environment variable loading
- `utils/` - Shared utilities (google_calendar, whatsapp, s3_logger, gmail_reader)
- `tests/` - Test suite
- `state.py` - Shared PlanBState TypedDict (single source of truth)
- `graph.py` - LangGraph pipeline definition
- `main.py` - FastAPI server, /webhook endpoint

## Agent Models

Use the right model for each agent to balance cost and quality:

- GROQ_MODEL_LARGE = 'llama-3.3-70b-versatile' → Context, Resilience, Orchestrator, Replan, Routine, Negotiate, Predictive Risk
- GROQ_MODEL_FAST = 'llama-3.1-8b-instant' → Monitor, Priority Engine, Comms
- Gemini gemini-1.5-flash → Monitor Agent only (reading and understanding Gmail emails)

## Shared State

All agents communicate through a single TypedDict called PlanBState defined in state.py. Every agent function takes PlanBState as input and returns PlanBState. Never pass data between agents directly — always read from and write to state.

## Key Integrations

- **LLM Providers**: Groq (via `langchain-groq`) and Google Gemini (via `langchain-google-genai`)
- **WhatsApp**: Meta WhatsApp Business API — send via utils/whatsapp.py
- **Google APIs**: OAuth2 with `credentials.json` — Calendar read/write, Gmail read
- **AWS**: S3 for audit logs, SES for sending reschedule emails, Lambda for deployment, EventBridge for scheduled triggers
- **Deployment**: AWS Lambda via Mangum adapter, region ap-south-1 (Mumbai)

## Configuration

- Environment variables in `.env` (not committed to git)
- Google OAuth credentials in `credentials.json` (not committed to git)
- OAuth token cached in `token.json` at runtime

## Key Rules

- Always load env vars using python-dotenv at the top of every file
- Never hardcode any API key or token anywhere
- Every agent file lives at agents/agentname/__init__.py
- Every agent function signature: def agent_name(state: PlanBState) -> PlanBState
- Google Calendar timezone is always Asia/Kolkata (IST, UTC+5:30)
- WhatsApp messages must be under 300 words, plain text, no markdown
- Git commit after every working agent: git add . && git commit -m "agent name done"
- Working hours for scheduling: 7am to 10pm IST only