"""The SMTP config the suites store before calling a route behind smtpConfigCheck."""

from __future__ import annotations

import os
from typing import Optional


def smtp_env() -> Optional[dict]:
    """Build an SMTP config from env, or None if not configured.

    CI provides SMTP_HOST/PORT/USERNAME/PASSWORD as secrets. Locally, point
    these at Mailpit (SMTP_HOST=mailpit SMTP_PORT=1025, no credentials).
    """
    host = os.getenv("SMTP_HOST")
    port = os.getenv("SMTP_PORT")
    if not host or not port:
        return None
    username = os.getenv("SMTP_USERNAME", "")
    return {
        "host": host,
        "port": int(port),
        "username": username,
        "password": os.getenv("SMTP_PASSWORD", ""),
        "fromEmail": os.getenv("SMTP_FROM_EMAIL") or username or "no-reply@example.com",
    }
