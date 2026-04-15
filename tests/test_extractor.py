import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from extractor import detect_source, extract_jd_link, classify


# ---------------------------------------------------------------------------
# detect_source
# ---------------------------------------------------------------------------

def _email(sender_domain="", body_text="", body_links=None):
    return {
        "sender_domain": sender_domain,
        "body_text": body_text,
        "body_links": body_links or [],
        "message_id": "test-id",
        "subject": "Test",
    }


def test_detect_source_sender_domain():
    result = detect_source(_email(sender_domain="linkedin.com"), jd_link=None)
    assert result["source"] == "LinkedIn"
    assert result["source_confidence"] == "auto"


def test_detect_source_subdomain():
    result = detect_source(_email(sender_domain="jobs.greenhouse.io"), jd_link=None)
    assert result["source"] == "Greenhouse"
    assert result["source_confidence"] == "auto"


def test_detect_source_body_keyword():
    result = detect_source(_email(body_text="You applied on LinkedIn via Easy Apply"), jd_link=None)
    assert result["source"] == "LinkedIn"
    assert result["source_confidence"] == "auto"


def test_detect_source_jd_link_inferred():
    result = detect_source(_email(), jd_link="https://jobs.lever.co/company/role")
    assert result["source"] == "Lever"
    assert result["source_confidence"] == "inferred"


def test_detect_source_fallback():
    result = detect_source(_email(), jd_link=None)
    assert result["source"] == "Direct / Unknown"
    assert result["source_confidence"] == "low"


def test_detect_all_domain_mappings():
    from extractor import SOURCE_DOMAIN_MAP
    for domain, expected_source in SOURCE_DOMAIN_MAP.items():
        result = detect_source(_email(sender_domain=domain), jd_link=None)
        assert result["source"] == expected_source, f"Failed for domain: {domain}"
        assert result["source_confidence"] == "auto"


# ---------------------------------------------------------------------------
# extract_jd_link — email body scan
# ---------------------------------------------------------------------------

def test_extract_jd_link_from_body():
    email = _email(body_links=["https://www.greenhouse.io/jobs/12345", "https://example.com"])
    result = extract_jd_link(email, "Acme", "Engineer")
    assert result["jd_link"] == "https://www.greenhouse.io/jobs/12345"
    assert result["link_source"] == "email"


def test_extract_jd_link_no_match_falls_back(monkeypatch):
    monkeypatch.setattr("extractor._web_search", lambda q: None)
    email = _email(body_links=["https://example.com/unrelated"])
    result = extract_jd_link(email, "Acme", "Engineer")
    assert result["jd_link"] is None
    assert result["link_source"] == "not_found"


def test_extract_jd_link_web_search_fallback(monkeypatch):
    monkeypatch.setattr("extractor._web_search", lambda q: "https://linkedin.com/jobs/view/123")
    email = _email()
    result = extract_jd_link(email, "Stripe", "Data Engineer")
    assert result["jd_link"] == "https://linkedin.com/jobs/view/123"
    assert result["link_source"] == "inferred"


# ---------------------------------------------------------------------------
# classify — mocked Claude API
# ---------------------------------------------------------------------------

def test_classify_returns_none_on_low_confidence(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_CONFIDENCE_THRESHOLD", 0.5)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"company":"Acme","role":"SWE","status_signal":"confirmation","confidence":0.3,"reasoning":"low conf"}')]

    with patch("extractor._get_client") as mock_client:
        mock_client.return_value.messages.create.return_value = mock_response
        email = _email(body_text="Thank you for applying")
        result = classify(email)
    assert result is None


def test_classify_returns_none_on_unknown(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_CONFIDENCE_THRESHOLD", 0.5)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"company":null,"role":null,"status_signal":"unknown","confidence":0.2,"reasoning":"newsletter"}')]

    with patch("extractor._get_client") as mock_client:
        mock_client.return_value.messages.create.return_value = mock_response
        result = classify(_email(body_text="Weekly job digest..."))
    assert result is None


def test_classify_success(monkeypatch):
    import config
    monkeypatch.setattr(config, "MIN_CONFIDENCE_THRESHOLD", 0.5)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"company":"Stripe","role":"Data Engineer","status_signal":"interview_request","confidence":0.94,"reasoning":"Recruiter scheduling interview"}')]

    with patch("extractor._get_client") as mock_client:
        mock_client.return_value.messages.create.return_value = mock_response
        result = classify(_email(subject="Interview invite", body_text="Hi, we'd like to schedule..."))

    assert result is not None
    assert result["company"] == "Stripe"
    assert result["status_signal"] == "interview_request"
