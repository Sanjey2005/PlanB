# PlanB — WhatsApp AI Scheduling Assistant

> Built for NeoVerse 26 Hackathon

PlanB is an autonomous AI scheduling assistant that operates entirely through WhatsApp. When disruptions hit — a cancelled meeting, a traffic jam, an urgent email — PlanB detects them, re-evaluates your entire day, reschedules meetings via Google Calendar, drafts rescheduling emails via AWS SES, and sends you a plain-text WhatsApp summary. No app to open. No manual rescheduling.

---

## Architecture

11 specialised LangGraph agents communicate through a single shared `PlanBState` TypedDict. The Orchestrator decides which agents fire at runtime based on disruption type and severity — the pipeline is never fixed.

### Agent Pipeline

```
Monitor → Context → Resilience → Orchestrator → Priority Engine
       → Replan → Routine → Scheduler → Negotiate → Comms
```

Predictive Risk Agent fires separately during `morning_briefing` and `evening_review` modes only.

### All Agents

| # | Agent | Model | Role |
|---|-------|-------|------|
| 1 | Monitor | Gemini 1.5 Flash + Llama 3.1 8B | Multi-source disruption detection (Gmail, WhatsApp, calendar) |
| 2 | Context | Llama 3.3 70B | Disruption classification and severity scoring |
| 3 | Resilience | Llama 3.3 70B | Cascade impact mapping across all calendar events |
| 4 | Orchestrator | Llama 3.3 70B | Adaptive runtime routing — decides which agents fire |
| 5 | Priority Engine | Llama 3.1 8B | Multi-factor task scoring 0–100 |
| 6 | Replan | Llama 3.3 70B | Keep / move / drop decisions for affected events |
| 7 | Routine | Llama 3.3 70B | Protect daily habits and recurring commitments |
| 8 | Scheduler | Llama 3.1 8B | Google Calendar read/write operations |
| 9 | Negotiate | Llama 3.3 70B | LLM email drafting + AWS SES sending |
| 10 | Comms | Llama 3.1 8B | WhatsApp summary generation and delivery |
| 11 | Predictive Risk | Llama 3.3 70B | Week-ahead proactive risk scanning |
| 12 | Crisis | Llama 3.3 70B | Emergency mode — clears non-essential events |
| 13 | Stress | Llama 3.3 70B | Detects overload and inserts recovery blocks |
| 14 | Undo | Llama 3.3 70B | Reverts last scheduling action |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Orchestration | LangGraph |
| Web Framework | FastAPI + Mangum (Lambda adapter) |
| LLM Providers | Groq (Llama 3.3 70B / 3.1 8B), Google Gemini 1.5 Flash |
| Messaging | Meta WhatsApp Business API |
| Calendar & Email | Google Calendar API, Gmail API (OAuth2) |
| Email Sending | AWS SES |
| Audit Logging | AWS S3 |
| Deployment | AWS Lambda + EventBridge, region ap-south-1 |
| Language | Python 3.11+ |

---

## Project Structure

```
planb/
├── main.py                  # FastAPI server, /webhook endpoint
├── graph.py                 # LangGraph pipeline definition
├── state.py                 # PlanBState TypedDict (single source of truth)
├── requirements.txt
├── agents/
│   ├── monitor/             # Disruption detection
│   ├── context/             # Classification
│   ├── resilience/          # Cascade mapping
│   ├── orchestrator/        # Runtime routing
│   ├── priority/            # Task scoring
│   ├── replan/              # Schedule decisions
│   ├── routine/             # Habit protection
│   ├── scheduler/           # Calendar writes
│   ├── negotiate/           # Email drafting + SES
│   ├── comms/               # WhatsApp output
│   ├── predictive/          # Risk scanning
│   ├── crisis/              # Emergency mode
│   ├── stress/              # Overload detection
│   ├── undo/                # Schedule revert
│   └── lifestyle/           # Lifestyle balancing
├── utils/
│   ├── google_calendar.py   # Calendar read/write helpers
│   ├── gmail_reader.py      # Gmail fetch helpers
│   ├── whatsapp.py          # WhatsApp send helpers
│   ├── s3_logger.py         # AWS S3 audit logging
│   ├── user_dna.py          # User preference profile
│   ├── habit_learner.py     # Habit pattern learning
│   ├── streak_tracker.py    # Streak tracking
│   ├── demo_data.py         # Demo calendar data
│   └── demo_runner.py       # Demo orchestration
├── config/
│   └── settings.py          # Env var loading
├── dashboard/
│   └── index.html           # Status dashboard
└── tests/                   # pytest test suite
```

---

## Setup

### Prerequisites
- Python 3.11+
- A Meta WhatsApp Business account + phone number
- Google Cloud project with Calendar and Gmail APIs enabled
- AWS account (S3 bucket, SES verified domain, Lambda)
- Groq API key, Google Gemini API key

### Installation

```bash
git clone https://github.com/Sanjey2005/PlanB.git
cd PlanB
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configuration

Copy the example env and fill in your keys:

```bash
cp .env.example .env
```

Required `.env` variables:

```
GROQ_API_KEY=
GEMINI_API_KEY=
WHATSAPP_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
VERIFY_TOKEN=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=ap-south-1
S3_BUCKET_NAME=
SES_SENDER_EMAIL=
USER_WHATSAPP_NUMBER=
USER_EMAIL=
```

Place your Google OAuth credentials at `credentials.json` in the project root.

### Run locally

```bash
uvicorn main:app --reload --port 8000
```

### Run tests

```bash
pytest tests/
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| GET | `/webhook` | WhatsApp webhook verification |
| POST | `/webhook` | Incoming WhatsApp messages |
| POST | `/trigger` | Manual pipeline trigger |
| GET | `/status` | Current pipeline state |

---

## Deployment (AWS Lambda)

```bash
pip install -r requirements.txt -t package/
cp -r agents config utils state.py graph.py main.py package/
cd package && zip -r ../planb-lambda.zip .
aws lambda update-function-code --function-name planb --zip-file fileb://../planb-lambda.zip
```

EventBridge rules trigger the pipeline automatically:
- `morning_briefing` — 7:00 AM IST daily
- `evening_review` — 9:00 PM IST daily

---

## Key Design Rules

- All agents share a single `PlanBState` TypedDict — no direct agent-to-agent data passing
- WhatsApp messages are always plain text, under 300 words
- Scheduling is restricted to 7 AM – 10 PM IST only
- Google Calendar timezone: `Asia/Kolkata` (UTC+5:30)
- Every agent signature: `def agent_name(state: PlanBState) -> PlanBState`
- All secrets loaded via `python-dotenv` — never hardcoded

---

## License

MIT License — see [LICENSE](LICENSE) for details.
