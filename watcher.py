"""Layer 1 — Gmail Watcher: polls inbox and returns unprocessed job emails."""

import base64
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from pathlib import Path

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

GMAIL_SEARCH_TERMS = [
    'subject:"application"',
    'subject:"interview"',
    'subject:"next steps"',
    'subject:"we regret"',
    'subject:"offer"',
    'subject:"assessment"',
    'subject:"your application"',
    'subject:"thank you for applying"',
]


def _get_gmail_service():
    creds = None
    token_path = Path(config.GMAIL_TOKEN_FILE)
    creds_path = Path(config.GMAIL_CREDENTIALS_FILE)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _load_state() -> dict:
    state_path = Path(config.STATE_FILE)
    if state_path.exists():
        return json.loads(state_path.read_text())
    default_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y/%m/%d")
    return {"last_run_timestamp": default_ts}


def _save_state(state: dict):
    Path(config.STATE_FILE).write_text(json.dumps(state, indent=2))


def _extract_body(msg_data: dict) -> tuple[str, list[str]]:
    """Return (plain_text, list_of_hrefs) from a Gmail message payload."""
    payload = msg_data.get("payload", {})
    body_text = ""
    body_links = []

    def _walk_parts(parts):
        nonlocal body_text
        for part in parts:
            mime = part.get("mimeType", "")
            data = part.get("body", {}).get("data", "")
            if mime == "text/plain" and data and not body_text:
                body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            elif mime == "text/html" and data:
                html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.startswith("http"):
                        body_links.append(href)
                if not body_text:
                    body_text = soup.get_text(separator=" ", strip=True)
            if part.get("parts"):
                _walk_parts(part["parts"])

    if payload.get("parts"):
        _walk_parts(payload["parts"])
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    return body_text[:4000], body_links


def _parse_sender_domain(sender: str) -> str:
    match = re.search(r"@([\w.\-]+)", sender)
    return match.group(1).lower() if match else ""


def fetch_job_emails() -> list[dict]:
    """Return list of unprocessed job-related email dicts since last run."""
    state = _load_state()
    last_run = state["last_run_timestamp"]

    search_query = (
        "(" + " OR ".join(GMAIL_SEARCH_TERMS) + ")"
        + f" after:{last_run}"
        + " -from:me"
    )

    logger.info(f"Gmail search query: {search_query}")

    service = _get_gmail_service()
    emails = []
    page_token = None

    while True:
        kwargs = {"userId": "me", "q": search_query, "maxResults": 50}
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.users().messages().list(**kwargs).execute()
        messages = result.get("messages", [])

        for msg_stub in messages:
            time.sleep(0.5)
            msg_data = (
                service.users()
                .messages()
                .get(userId="me", id=msg_stub["id"], format="full")
                .execute()
            )

            headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "")
            subject = headers.get("Subject", "")
            date_str = headers.get("Date", "")

            # Parse timestamp
            try:
                from email.utils import parsedate_to_datetime
                ts = parsedate_to_datetime(date_str).isoformat()
            except Exception:
                ts = datetime.now(timezone.utc).isoformat()

            body_text, body_links = _extract_body(msg_data)
            message_id = msg_stub["id"]
            thread_id = msg_data.get("threadId", "")

            emails.append({
                "message_id": message_id,
                "thread_id": thread_id,
                "sender": sender,
                "sender_domain": _parse_sender_domain(sender),
                "subject": subject,
                "body_text": body_text,
                "body_links": body_links,
                "timestamp": ts,
                "gmail_link": f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
            })

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    # Update state after successful fetch
    new_ts = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    _save_state({"last_run_timestamp": new_ts})

    logger.info(f"Fetched {len(emails)} job-related emails")
    return emails
