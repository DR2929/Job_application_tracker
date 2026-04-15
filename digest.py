"""Layer 5 — Daily Digest: build summary and send via Gmail."""

import base64
import logging
from collections import Counter
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from notion_client import Client  # notion-client package
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

import config

logger = logging.getLogger(__name__)


def _get_notion() -> Client:
    return Client(auth=config.NOTION_API_KEY)


def _get_gmail_service():
    from watcher import _get_gmail_service as _svc
    return _svc()


# ---------------------------------------------------------------------------
# Notion queries
# ---------------------------------------------------------------------------

def _query_today(notion: Client, today: str) -> list[dict]:
    """Return rows applied today OR updated today with a status change."""
    applied_today = notion.data_sources.query(
        config.NOTION_DATASOURCE_ID,
        filter={"property": "Applied Date", "date": {"equals": today}},
    ).get("results", [])

    moved_today = notion.data_sources.query(
        config.NOTION_DATASOURCE_ID,
        filter={
            "and": [
                {"property": "Last Updated", "date": {"equals": today}},
                {"property": "Status", "status": {"does_not_equal": "Applied"}},
            ]
        },
    ).get("results", [])

    # Merge, deduplicate by page id
    seen = set()
    combined = []
    for page in applied_today + moved_today:
        if page["id"] not in seen:
            seen.add(page["id"])
            combined.append(page)
    return combined


def _query_this_week(notion: Client) -> int:
    from datetime import timedelta
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    results = notion.data_sources.query(
        config.NOTION_DATASOURCE_ID,
        filter={"property": "Applied Date", "date": {"on_or_after": week_ago}},
    ).get("results", [])
    return len(results)


def _prop(page: dict, name: str) -> str | None:
    props = page.get("properties", {})
    prop = props.get(name, {})
    ptype = prop.get("type")
    if ptype == "title":
        items = prop.get("title", [])
        return items[0]["plain_text"] if items else None
    if ptype == "rich_text":
        items = prop.get("rich_text", [])
        return items[0]["plain_text"] if items else None
    if ptype == "select":
        sel = prop.get("select")
        return sel["name"] if sel else None
    return None


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------

def build_digest() -> str:
    notion = _get_notion()
    today = date.today().isoformat()
    pages = _query_today(notion, today)
    week_total = _query_this_week(notion)

    # Applied today = pages with Applied Date == today
    applied_today = [
        p for p in pages
        if (p.get("properties", {}).get("Applied Date", {}).get("date") or {}).get("start", "")[:10] == today
    ]

    source_counter: Counter = Counter()
    for p in applied_today:
        source = _prop(p, "Source") or "Other"
        source_counter[source] += 1

    # Stage movements = updated today and status != Applied
    movements = [
        p for p in pages
        if _prop(p, "Status") not in (None, "Applied")
        and (p.get("properties", {}).get("Last Updated", {}).get("date") or {}).get("start", "")[:10] == today
    ]

    # Rows needing attention
    low_confidence = sum(1 for p in pages if _prop(p, "Source Confidence") == "low")
    missing_jd = sum(1 for p in pages if not (p.get("properties", {}).get("JD Link", {}).get("url")))

    # Role breakdown for today's applications
    role_counter: Counter = Counter()
    for p in applied_today:
        role = _prop(p, "Role") or "Unknown Role"
        role_counter[role] += 1

    # Build text body
    lines = [
        f"Job Tracker — {today}",
        "─" * 37,
        f"Applications today: {len(applied_today)}",
        "",
        "By source:",
    ]
    for source, count in sorted(source_counter.items()):
        lines.append(f"  {source:<28} {count}")

    lines += ["", "By role:"]
    for role, count in role_counter.most_common():
        lines.append(f"  {role:<40} {count}")

    lines += ["", "Applied today:"]
    if applied_today:
        for p in applied_today:
            company = _prop(p, "Company") or "?"
            role = _prop(p, "Role") or "?"
            source = _prop(p, "Source") or "?"
            lines.append(f"  • {company} — {role}  [{source}]")
    else:
        lines.append("  (none)")

    lines += ["", "Stage movements today:"]
    if movements:
        for p in movements:
            company = _prop(p, "Company") or "?"
            role = _prop(p, "Role") or "?"
            status = _prop(p, "Status") or "?"
            lines.append(f"  • {company} – {role} → {status}")
    else:
        lines.append("  (none)")

    lines += ["", "Needs your attention:"]
    if low_confidence:
        lines.append(f"  • {low_confidence} row(s) have source_confidence: low (manual tag needed)")
    if missing_jd:
        lines.append(f"  • {missing_jd} row(s) missing JD link")
    if not low_confidence and not missing_jd:
        lines.append("  (nothing flagged)")

    lines += [
        "",
        f"This week: {week_total} total applications",
        "─" * 37,
        f"View full tracker → https://notion.so/{config.NOTION_DATABASE_ID.replace('-', '')}",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Gmail send
# ---------------------------------------------------------------------------

def send_digest():
    today = date.today().isoformat()
    body = build_digest()
    n_apps = [l for l in body.splitlines() if l.startswith("Applications today:")][0]
    n = n_apps.split(":")[-1].strip()

    subject = f"Job tracker — {today} · {n} apps sent"

    if config.DRY_RUN:
        logger.info(f"[DRY RUN] Would send digest to {config.DIGEST_RECIPIENT}")
        logger.info(body)
        return

    if not config.DIGEST_RECIPIENT:
        logger.warning("DIGEST_RECIPIENT not configured; skipping send")
        return

    service = _get_gmail_service()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["To"] = config.DIGEST_RECIPIENT
    msg.attach(MIMEText(body, "plain"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    logger.info(f"Digest sent to {config.DIGEST_RECIPIENT}")
