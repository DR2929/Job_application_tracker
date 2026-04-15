import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from router import resolve_status


def test_basic_mapping():
    assert resolve_status("confirmation") == "Applied"
    assert resolve_status("assessment") == "OA / Screen"
    assert resolve_status("interview_request") == "Interview"
    assert resolve_status("offer") == "Offer"
    assert resolve_status("rejection") == "Rejected"


def test_progression_forward():
    assert resolve_status("interview_request", "Applied") == "Interview"
    assert resolve_status("offer", "Interview") == "Offer"


def test_no_downgrade():
    assert resolve_status("confirmation", "Interview") == "Interview"
    assert resolve_status("assessment", "Interview") == "Interview"


def test_rejection_is_terminal_from_any_stage():
    assert resolve_status("rejection", "Applied") == "Rejected"
    assert resolve_status("rejection", "Interview") == "Rejected"
    assert resolve_status("rejection", "Offer") == "Rejected"


def test_unknown_signal_keeps_current():
    result = resolve_status("totally_unknown", "Applied")
    assert result == "Applied"


def test_no_current_status():
    assert resolve_status("assessment", None) == "OA / Screen"
