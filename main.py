"""Entrypoint — orchestrates the full Track Agent pipeline."""

import json
import logging
import logging.handlers
import sys
from pathlib import Path

import schedule
import time

import config
from watcher import fetch_job_emails
from extractor import process_email
from notion_sync import upsert_application, write_failed_queue, retry_failed_queue
from digest import send_digest

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging():
    fmt = logging.Formatter(
        '{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","msg":%(message)s}'
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File
    fh = logging.handlers.RotatingFileHandler(
        config.LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline():
    logger.info('"Starting Track Agent pipeline"')
    if config.DRY_RUN:
        logger.info('"DRY_RUN is ON — no writes will occur"')

    # Retry any previously failed records first
    retry_failed_queue()

    emails = fetch_job_emails()
    logger.info(f'"Fetched {len(emails)} emails"')

    processed = 0
    skipped = 0

    for email in emails:
        record = process_email(email)
        if record is None:
            skipped += 1
            continue

        try:
            upsert_application(record)
            processed += 1
        except Exception as exc:
            logger.error(f'"Notion upsert failed: {exc}"')
            write_failed_queue(record)

    logger.info(f'"Pipeline complete — processed={processed} skipped={skipped}"')


def run_once():
    run_pipeline()


def run_daemon():
    """Run pipeline on a schedule: poll every GMAIL_POLL_INTERVAL_MINUTES, digest at DIGEST_SEND_TIME."""
    _setup_logging()
    logger.info(f'"Starting daemon mode (poll every {config.GMAIL_POLL_INTERVAL_MINUTES}m)"')

    schedule.every(config.GMAIL_POLL_INTERVAL_MINUTES).minutes.do(run_pipeline)
    schedule.every().day.at(config.DIGEST_SEND_TIME).do(send_digest)

    # Run once immediately on start
    run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(30)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _setup_logging()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"

    if cmd == "once":
        run_once()
    elif cmd == "daemon":
        run_daemon()
    elif cmd == "digest":
        send_digest()
    else:
        print(f"Usage: python main.py [once|daemon|digest]")
        sys.exit(1)
