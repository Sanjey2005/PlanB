"""
Negotiate Agent — PlanB Scheduling Assistant

Handles reschedule communication when meetings with external attendees are moved.
For each moved meeting, this agent:
  1. Determines the appropriate email tone (casual for teammates, professional for
     external/client contacts) by comparing attendee email domains against SES_FROM_EMAIL.
  2. Drafts a concise reschedule email body using Groq LLM (llama-3.3-70b-versatile).
  3. Generates a subject line based on tone.
  4. Sends the email immediately via AWS SES (autonomous mode) or stages it as a draft
     (assisted/advisory mode) depending on state["delegation_depth"].
  5. Writes the list of email actions to state["emails_sent"].

LangGraph node — reads from and writes to PlanBState only.
"""

from dotenv import load_dotenv

load_dotenv()

import boto3
from langchain_groq import ChatGroq

from state import PlanBState
from config.settings import GROQ_MODEL_LARGE, GROQ_API_KEY, SES_FROM_EMAIL, AWS_REGION


def _get_domain(email: str) -> str:
    """Extract the domain part from an email address."""
    return email.strip().lower().split("@")[-1] if "@" in email else ""


def _determine_tone(attendee_emails: list, from_domain: str) -> str:
    """Return 'casual' if all attendees share the sender domain, else 'professional'."""
    for email in attendee_emails:
        if _get_domain(email) != from_domain:
            return "professional"
    return "casual"


def _draft_email(llm: ChatGroq, task_name: str, old_time: str, new_time: str,
                 disruption_type: str, tone: str) -> str:
    """Use Groq LLM to draft a reschedule email body."""
    prompt = (
        f"Draft a reschedule email for a moved meeting.\n\n"
        f"Meeting: {task_name}\n"
        f"Original time: {old_time}\n"
        f"New proposed time: {new_time}\n"
        f"Reason (keep vague, professional): {disruption_type}\n"
        f"Tone: {tone} (casual = teammate, professional = client/external)\n\n"
        f"Rules:\n"
        f"- Keep it under 100 words\n"
        f"- Do not over-apologise\n"
        f"- For casual tone: friendly, direct, no formal salutation needed\n"
        f"- For professional tone: polite opener, brief apology, clear new time, offer to adjust\n"
        f"- Do not mention specific personal details about the disruption\n"
        f"- End with an offer to confirm or suggest another time\n\n"
        f"Return ONLY the email body text. No subject line. No JSON."
    )
    response = llm.invoke(prompt)
    return response.content.strip()


def _generate_subject(tone: str, task_name: str) -> str:
    """Generate a subject line based on tone."""
    if tone == "casual":
        return f"Quick reschedule — {task_name}"
    return f"Meeting Rescheduled: {task_name}"


def _send_ses_email(ses_client, from_email: str, to_email: str,
                    subject: str, body: str) -> str:
    """Send an email via AWS SES. Returns 'sent' or 'failed'."""
    try:
        ses_client.send_email(
            Source=from_email,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
        return "sent"
    except Exception as e:
        print(f"[Negotiate] SES send failed for {to_email}: {e}")
        return "failed"


def negotiate_agent(state: PlanBState) -> PlanBState:
    """Negotiate Agent — drafts and optionally sends reschedule emails for moved meetings.

    Reads:
        state['moved_meetings']   — list of moved event dicts with attendees, task_name,
                                     old_time, new_time fields.
        state['delegation_depth'] — 'autonomous' sends immediately; 'assisted'/'advisory'
                                     stages as draft.
        state['context_summary']  — disruption context for email drafting.
        state['disruption_raw']   — fallback disruption context.
        state['disruption_type']  — type of disruption for the email reason line.

    Writes:
        state['emails_sent'] — list of dicts with keys: to, subject, body, status, meeting.

    Returns:
        Updated PlanBState.
    """
    try:
        moved_meetings = state.get("moved_meetings") or []
        delegation_depth = state.get("delegation_depth") or "assisted"

        if not moved_meetings:
            return state

        # Initialise LLM
        llm = ChatGroq(
            model_name=GROQ_MODEL_LARGE,
            api_key=GROQ_API_KEY,
            temperature=0.4,
        )

        from_domain = _get_domain(SES_FROM_EMAIL) if SES_FROM_EMAIL else ""
        disruption_type = state.get("disruption_type") or "a scheduling conflict"
        emails_sent = []

        # SES client — only created when needed
        ses_client = None
        if delegation_depth == "autonomous":
            ses_client = boto3.client("ses", region_name=AWS_REGION)

        for meeting in moved_meetings:
            try:
                attendees = meeting.get("attendees") or []
                task_name = meeting.get("task_name", "Meeting")
                old_time = meeting.get("old_time", "TBD")
                new_time = meeting.get("new_time", "TBD")

                if not attendees:
                    continue

                # Determine tone
                tone = _determine_tone(attendees, from_domain)

                # Draft email body via LLM
                body = _draft_email(llm, task_name, old_time, new_time,
                                    disruption_type, tone)

                # Generate subject
                subject = _generate_subject(tone, task_name)

                # Send or stage for each attendee
                for email_addr in attendees:
                    if delegation_depth == "autonomous":
                        status = _send_ses_email(ses_client, SES_FROM_EMAIL,
                                                 email_addr, subject, body)
                    else:
                        status = "draft_ready"

                    emails_sent.append({
                        "to": email_addr,
                        "subject": subject,
                        "body": body,
                        "status": status,
                        "meeting": task_name,
                    })

            except Exception as e:
                print(f"[Negotiate] Error processing meeting '{meeting.get('task_name', '?')}': {e}")
                continue

        state["emails_sent"] = emails_sent
        print(f"[Negotiate] Processed {len(emails_sent)} email(s) for {len(moved_meetings)} moved meeting(s)")

    except Exception as e:
        print(f"[Negotiate] Agent error: {e}")

    return state
