"""Read mail caught by Mailpit, the SMTP sink the integration stack runs.

The Python counterpart of ``frontend/tests/e2e/helpers/mailpit.helper.ts``. CI
publishes Mailpit's web API on the runner at port 8025; ``MAILPIT_URL``
overrides it.

A test snapshots the mailbox before the action that sends mail and then waits
for a message that was not there before. Timestamps are not used for this:
Mailpit's clock and the test's clock are different machines on some setups,
and an earlier message to the same address would otherwise be read instead.
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass

import requests

DEFAULT_TIMEOUT_SECONDS = 60.0
_POLL_INTERVAL_SECONDS = 2.0
_REQUEST_TIMEOUT_SECONDS = 10

_RESET_LINK_TOKEN = re.compile(r"/reset-password#token=([A-Za-z0-9._-]+)")
# The code sits alone in the sign-in email. Colours such as #000000 in inline
# styles are excluded by the leading-# check once tags are stripped.
_SIX_DIGIT_CODE = re.compile(r"(?<![\d#])(\d{6})(?!\d)")


def mailpit_url() -> str:
    return (os.getenv("MAILPIT_URL") or "http://localhost:8025").rstrip("/")


@dataclass(frozen=True)
class Message:
    id: str
    subject: str
    html: str
    text: str

    @property
    def visible_text(self) -> str:
        """The body as a reader sees it: text part, or HTML with markup removed."""
        if self.text.strip():
            return self.text
        body = re.sub(r"(?is)<(head|style|script)\b.*?</\1>", " ", self.html)
        return html.unescape(re.sub(r"(?s)<[^>]+>", " ", body))


class MailpitUnavailable(RuntimeError):
    """Mailpit could not be reached, so no assertion about mail is possible."""


def _search(address: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{mailpit_url()}/api/v1/search",
            params={"query": f'to:"{address}"'},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise MailpitUnavailable(
            f"Mailpit is not reachable at {mailpit_url()}: {exc}"
        ) from exc
    if resp.status_code != 200:
        raise MailpitUnavailable(
            f"Mailpit search at {mailpit_url()} answered HTTP {resp.status_code}"
        )
    return list(resp.json().get("messages") or [])


def message_ids(address: str) -> set[str]:
    """IDs of every message already delivered to ``address``."""
    return {str(m.get("ID")) for m in _search(address)}


def _read(message_id: str) -> Message:
    resp = requests.get(
        f"{mailpit_url()}/api/v1/message/{message_id}",
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    body = resp.json()
    return Message(
        id=message_id,
        subject=str(body.get("Subject") or ""),
        html=str(body.get("HTML") or ""),
        text=str(body.get("Text") or ""),
    )


def wait_for_new_message(
    address: str,
    subject_contains: str,
    seen: set[str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Message:
    """Wait for a message to ``address`` whose ID is not in ``seen``.

    Raises ``AssertionError`` naming what did arrive, so a missing email reads
    as the failure it is rather than as a timeout.
    """
    deadline = time.monotonic() + timeout
    subjects: list[str] = []
    while time.monotonic() < deadline:
        fresh = [m for m in _search(address) if str(m.get("ID")) not in seen]
        subjects = [str(m.get("Subject") or "") for m in fresh]
        for summary in fresh:
            if subject_contains.lower() in str(summary.get("Subject") or "").lower():
                return _read(str(summary.get("ID")))
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"No email with subject containing {subject_contains!r} reached "
        f"{address} within {timeout:.0f}s. New messages seen: {subjects or 'none'}."
    )


def reset_link_token(message: Message) -> str:
    """The token carried by the reset link in a password-reset email."""
    match = _RESET_LINK_TOKEN.search(message.html) or _RESET_LINK_TOKEN.search(
        message.text
    )
    if not match:
        raise AssertionError(
            f"The email {message.subject!r} has no password-reset link."
        )
    return match.group(1)


def sign_in_code(message: Message) -> str:
    """The six-digit code in a sign-in (OTP) email."""
    codes = set(_SIX_DIGIT_CODE.findall(message.visible_text))
    if len(codes) != 1:
        raise AssertionError(
            f"Expected exactly one six-digit code in {message.subject!r}, "
            f"found {len(codes)}."
        )
    return codes.pop()
