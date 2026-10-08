"""Both /api/v1/mail routes answer 500 to a malformed JSON body.

The JSON body parser runs before the router and its failure is reported as an
internal error, so the request never reaches the SMTP or token checks.
"""

from __future__ import annotations

import pytest
from mail_audit_support import (
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    SEND_EMAIL_ROUTE,
    UPDATE_SMTP_CONFIG_ROUTE,
    MailClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("sub_path", "route"),
    [
        ("/emails/sendEmail", SEND_EMAIL_ROUTE),
        ("/updateSmtpConfig", UPDATE_SMTP_CONFIG_ROUTE),
    ],
    ids=["send_email", "update_smtp_config"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    mail_client: MailClient, sub_path: str, route: str
) -> None:
    # API bug: a body that does not parse is a caller mistake, yet it answers 500.
    resp = mail_client.post(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
