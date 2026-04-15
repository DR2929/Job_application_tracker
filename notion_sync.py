"""Layer 4 — Notion Upsert: create or update application rows."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from notion_client import Client

import config
from router import resolve_status

logger = logging.getLogger(__name__)

_notion = None


def _get_notion() -> Client:
    global _notion
    if _notion is None:
        _notion = Client(auth=config.NOTION_API_KEY)
    return _notion


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _find_existing_page(company: str, role: str) -> dict | None:
    """Return the first Notion page matching (company, role), or None."""
    notion = _get_notion()
    response = notion.data_sources.query(
        config.NOTION_DATASOURCE_ID,
        filter={
            "and": [
                {
                    "property": "Company",
                    "title": {"equals": company},
                },
                {
                    "property": "Role",
                    "rich_text": {"equals": role},
                },
            ]
        },
    )
    results = response.get("results", [])
    return results[0] if results else None


def _get_prop_value(page: dict, prop_name: str) -> str | None:
    """Extract a simple string value from a Notion page property."""
    props = page.get("properties", {})
    prop = props.get(prop_name, {})
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
    if ptype == "status":
        sel = prop.get("status")
        return sel["name"] if sel else None
    if ptype == "url":
        return prop.get("url")
    if ptype == "date":
        date_obj = prop.get("date")
        return date_obj["start"] if date_obj else None
    return None


# ---------------------------------------------------------------------------
# Notion property builders
# ---------------------------------------------------------------------------

def _title(value: str) -> dict:
    return {"title": [{"text": {"content": value or ""}}]}

def _text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value or ""}}]}

def _select(value: str) -> dict:
    return {"select": {"name": value}}

def _status(value: str) -> dict:
    return {"status": {"name": value}}

def _url(value: str | None) -> dict:
    return {"url": value}

def _date(iso_str: str) -> dict:
    # Notion wants YYYY-MM-DD
    try:
        dt = datetime.fromisoformat(iso_str)
        return {"date": {"start": dt.date().isoformat()}}
    except Exception:
        return {"date": {"start": iso_str[:10]}}


# ---------------------------------------------------------------------------
# Main upsert
# ---------------------------------------------------------------------------

def upsert_application(record: dict) -> str:
    """
    Create or update a Notion row for the given application record.
    Returns the Notion page_id.

    record keys: company, role, status_signal, jd_link, link_source,
                 source, source_confidence, applied_date, email_link
    """
    company = record.get("company", "")
    role = record.get("role", "")
    now_iso = datetime.now(timezone.utc).isoformat()

    if config.DRY_RUN:
        logger.info(f"[DRY RUN] upsert_application: {company!r} / {role!r} → {record.get('status_signal')}")
        return "dry-run-page-id"

    existing = _find_existing_page(company, role)

    if existing is None:
        return _create_page(record, now_iso)
    else:
        return _update_page(existing, record, now_iso)


def _create_page(record: dict, now_iso: str) -> str:
    notion = _get_notion()
    new_status = resolve_status(record["status_signal"])
    source_conf = record.get("source_confidence", "low")

    properties = {
        "Company": _title(record.get("company", "")),
        "Role": _text(record.get("role", "")),
        "Status": _status(new_status),
        "Applied Date": _date(record.get("applied_date", now_iso)),
        "Last Updated": _date(now_iso),
        "Source": _select(record.get("source", "Direct / Unknown")),
        "Source Confidence": _select(source_conf),
        "Email Link": _url(record.get("email_link")),
    }
    if record.get("jd_link"):
        properties["JD Link"] = _url(record["jd_link"])

    page = notion.pages.create(
        parent={"database_id": config.NOTION_DATABASE_ID},
        properties=properties,
    )
    page_id = page["id"]
    logger.info(f"Created Notion page {page_id} for {record['company']} / {record['role']}")
    return page_id


def _update_page(existing: dict, record: dict, now_iso: str) -> str:
    notion = _get_notion()
    page_id = existing["id"]

    current_status = _get_prop_value(existing, "Status")
    new_status = resolve_status(record["status_signal"], current_status)

    updates: dict = {
        "Status": _status(new_status),
        "Last Updated": _date(now_iso),
    }

    # Fill null fields — never overwrite existing non-null values
    if not _get_prop_value(existing, "Applied Date"):
        updates["Applied Date"] = _date(record.get("applied_date", now_iso))

    if not _get_prop_value(existing, "JD Link") and record.get("jd_link"):
        updates["JD Link"] = _url(record["jd_link"])

    if not _get_prop_value(existing, "Email Link") and record.get("email_link"):
        updates["Email Link"] = _url(record["email_link"])

    # Never overwrite Source if manually set
    existing_source_conf = _get_prop_value(existing, "Source Confidence")
    if existing_source_conf != "manual":
        if not _get_prop_value(existing, "Source"):
            updates["Source"] = _select(record.get("source", "Direct / Unknown"))
            updates["Source Confidence"] = _select(record.get("source_confidence", "low"))

    notion.pages.update(page_id=page_id, properties=updates)
    logger.info(f"Updated Notion page {page_id} for {record['company']} / {record['role']}")
    return page_id


# ---------------------------------------------------------------------------
# Failed queue
# ---------------------------------------------------------------------------

def write_failed_queue(record: dict):
    queue_path = Path(config.FAILED_QUEUE_FILE)
    entries = []
    if queue_path.exists():
        try:
            entries = json.loads(queue_path.read_text())
        except Exception:
            entries = []
    entries.append(record)
    queue_path.write_text(json.dumps(entries, indent=2))
    logger.warning(f"Wrote failed record to {config.FAILED_QUEUE_FILE}: {record.get('company')}/{record.get('role')}")


def retry_failed_queue():
    queue_path = Path(config.FAILED_QUEUE_FILE)
    if not queue_path.exists():
        return
    entries = json.loads(queue_path.read_text())
    remaining = []
    for record in entries:
        try:
            upsert_application(record)
        except Exception as exc:
            logger.error(f"Retry failed for {record.get('company')}: {exc}")
            remaining.append(record)
    queue_path.write_text(json.dumps(remaining, indent=2))
