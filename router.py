"""Layer 3 — Status Router: maps classifier signal to Notion status label."""

import logging

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "confirmation": "Applied",
    "assessment": "OA / Screen",
    "interview_request": "Interview",
    "offer": "Offer",
    "rejection": "Rejected",
}

# Progression order (higher index = more advanced)
STATUS_ORDER = ["Applied", "OA / Screen", "Interview", "Offer", "Rejected"]


def resolve_status(status_signal: str, current_status: str | None = None) -> str:
    """
    Map status_signal to a Notion status label, respecting the progression rule.
    Never downgrades an existing status (except Rejected, which is terminal at any stage).
    """
    new_status = STATUS_MAP.get(status_signal)
    if new_status is None:
        logger.warning(f"Unknown status_signal: {status_signal!r}")
        return current_status or "Applied"

    if current_status is None:
        return new_status

    # Rejected is terminal — always honour it
    if new_status == "Rejected":
        return "Rejected"

    # Never downgrade
    try:
        current_idx = STATUS_ORDER.index(current_status)
        new_idx = STATUS_ORDER.index(new_status)
    except ValueError:
        return new_status

    if new_idx <= current_idx:
        logger.info(
            f"Progression guard: keeping {current_status!r} over {new_status!r}"
        )
        return current_status

    return new_status
