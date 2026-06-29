from typing import Optional


def domain_from_email(email: Optional[str]) -> str:
    """Lower-cased domain part of an email, or ``"unknown"`` when absent."""
    if not email or "@" not in email:
        return "unknown"
    return email.rsplit("@", 1)[1].strip().lower() or "unknown"
