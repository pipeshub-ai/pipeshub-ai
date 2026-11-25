"""WP-22 TG-12: the personal-data shapes an org's redaction masks, and what it leaves alone."""

from __future__ import annotations

import pytest

from harness_core.security.personal_data import redact_personal, redact_personal_in
from harness_core.security.token_patterns import SECRET_MASK


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("write to a.b+tag@mail.example.co.uk today", "email"),
        ("call +44 20 7946 0958", "phone"),
        ("call (415) 555-0100", "phone"),
        ("call +14155550100", "phone"),
        ("card 4111-1111-1111-1111", "payment_card"),
        ("ssn 123-45-6789", "ssn"),
        ("iban GB82 WEST 1234 5698 7654 32", "iban"),
    ],
)
def test_tg12_each_shape_is_masked_and_named(text: str, kind: str) -> None:
    found: set[str] = set()
    masked = redact_personal(text, found)
    assert found == {kind}
    assert f"[redacted {kind.replace('_', ' ')}]" in masked


@pytest.mark.parametrize(
    "text",
    [
        "released 2026-10-28 at 14:05",
        "order 123456789012 shipped",
        "card-like 4111 1111 1111 1112 fails the Luhn check",
        "price 1,234,567.89 USD",
        "version 10.2.3.4",
        "ticket PROJ-1234 and PR #4313",
    ],
)
def test_tg12_ids_dates_and_numbers_are_not_personal_data(text: str) -> None:
    found: set[str] = set()
    assert redact_personal(text, found) == text
    assert found == set()


def test_tg12_credentials_are_masked_too_and_json_keys_stay() -> None:
    found: set[str] = set()
    value = redact_personal_in(
        {"owner_email": "x@y.io", "token": "ghp_" + "a" * 36, "rows": [{"phone": "+1 415 555 0100"}]}, found
    )
    assert value == {"owner_email": "[redacted email]", "token": SECRET_MASK, "rows": [{"phone": "[redacted phone]"}]}
    assert found == {"email", "secret", "phone"}
