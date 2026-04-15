"""Layer 2 — Extraction: classify email, extract JD link, detect source."""

import json
import logging
from pathlib import Path

import anthropic
import httpx

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JOB_BOARD_DOMAINS = [
    "linkedin.com/jobs",
    "dice.com",
    "indeed.com/viewjob",
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "icims.com",
    "taleo.net",
    "jobvite.com",
    "ashbyhq.com",
]

SOURCE_DOMAIN_MAP = {
    "linkedin.com": "LinkedIn",
    "dice.com": "Dice",
    "indeed.com": "Indeed",
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "myworkdayjobs.com": "Workday",
    "smartrecruiters.com": "SmartRecruiters",
    "icims.com": "iCIMS",
    "jobvite.com": "Jobvite",
    "ashbyhq.com": "Ashby",
}

BODY_KEYWORDS = {
    "via LinkedIn": "LinkedIn",
    "Easy Apply": "LinkedIn",
    "your Dice application": "Dice",
    "applied through Indeed": "Indeed",
    "applied on LinkedIn": "LinkedIn",
    "applied on Indeed": "Indeed",
}

CLASSIFIER_PROMPT = Path(__file__).parent / "prompts" / "classifier.txt"

_anthropic_client = None


def _get_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


# ---------------------------------------------------------------------------
# 2a. Email Classifier
# ---------------------------------------------------------------------------

def classify(email: dict) -> dict | None:
    """
    Call Claude to classify a job email.
    Returns structured dict or None if below confidence threshold / unknown.
    """
    system_prompt = CLASSIFIER_PROMPT.read_text()
    user_content = email["subject"] + "\n\n" + email["body_text"]

    try:
        response = _get_client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=config.CLASSIFIER_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text.strip()
        logger.debug(f"Classifier raw response for {email['message_id']}: {raw!r}")

        # Strip markdown code fences if Claude wrapped the JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        if not raw:
            logger.error(f"Classifier returned empty response for message {email['message_id']} (stop_reason={response.stop_reason!r})")
            return None

        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"Classifier JSON parse error for message {email['message_id']}: {exc} — raw={raw!r}")
        return None
    except Exception as exc:
        logger.error(f"Classifier error for message {email['message_id']}: {exc}")
        return None

    confidence = result.get("confidence", 0.0)
    status_signal = result.get("status_signal", "unknown")

    if confidence < config.MIN_CONFIDENCE_THRESHOLD or status_signal == "unknown":
        _log_skipped(email, result)
        return None

    return result


def _log_skipped(email: dict, result: dict):
    line = json.dumps({
        "message_id": email["message_id"],
        "subject": email["subject"],
        "classification": result,
    })
    with open(config.SKIPPED_LOG_FILE, "a") as f:
        f.write(line + "\n")
    logger.info(f"Skipped email {email['message_id']}: {result.get('reasoning', '')}")


# ---------------------------------------------------------------------------
# 2b. JD Link Extractor
# ---------------------------------------------------------------------------

def extract_jd_link(email: dict, company: str, role: str) -> dict:
    """
    Return {"jd_link": url_or_none, "link_source": "email"|"inferred"|"not_found"}.
    """
    # Step 1 — scan email body links
    for link in email.get("body_links", []):
        for domain in JOB_BOARD_DOMAINS:
            if domain in link:
                return {"jd_link": link, "link_source": "email"}

    # Step 2 — web search fallback
    query = f'"{company}" "{role}" job site:linkedin.com OR site:greenhouse.io OR site:lever.co'
    try:
        result_url = _web_search(query)
        if result_url:
            return {"jd_link": result_url, "link_source": "inferred"}
    except Exception as exc:
        logger.warning(f"Web search failed for {company}/{role}: {exc}")

    return {"jd_link": None, "link_source": "not_found"}


def _web_search(query: str) -> str | None:
    """Minimal DuckDuckGo instant-answer search; returns first URL or None."""
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; TrackAgent/1.0)"},
            timeout=10,
        )
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if href.startswith("http"):
                return href
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 2c. Source Detector
# ---------------------------------------------------------------------------

def detect_source(email: dict, jd_link: str | None) -> dict:
    """
    Return {"source": str, "source_confidence": "auto"|"inferred"|"low"}.
    """
    sender_domain = email.get("sender_domain", "")

    # 1. Sender domain match
    for domain, source in SOURCE_DOMAIN_MAP.items():
        if sender_domain.endswith(domain):
            return {"source": source, "source_confidence": "auto"}

    # 2. Body keyword scan
    body = email.get("body_text", "")
    for keyword, source in BODY_KEYWORDS.items():
        if keyword.lower() in body.lower():
            return {"source": source, "source_confidence": "auto"}

    # 3. JD link domain
    if jd_link:
        for domain, source in SOURCE_DOMAIN_MAP.items():
            if domain in jd_link:
                return {"source": source, "source_confidence": "inferred"}

    return {"source": "Direct / Unknown", "source_confidence": "low"}


# ---------------------------------------------------------------------------
# Public: process one email through all three sub-steps
# ---------------------------------------------------------------------------

def process_email(email: dict) -> dict | None:
    """
    Run classify → extract_jd_link → detect_source.
    Returns a merged record ready for the router, or None if skipped.
    """
    classification = classify(email)
    if classification is None:
        return None

    company = classification.get("company") or ""
    role = classification.get("role") or ""

    jd_info = extract_jd_link(email, company, role)
    source_info = detect_source(email, jd_info.get("jd_link"))

    return {
        # from classifier
        "company": company,
        "role": role,
        "status_signal": classification["status_signal"],
        "confidence": classification["confidence"],
        "reasoning": classification.get("reasoning", ""),
        # from JD extractor
        "jd_link": jd_info["jd_link"],
        "link_source": jd_info["link_source"],
        # from source detector
        "source": source_info["source"],
        "source_confidence": source_info["source_confidence"],
        # from email metadata
        "applied_date": email["timestamp"],
        "email_link": email["gmail_link"],
        "message_id": email["message_id"],
    }
