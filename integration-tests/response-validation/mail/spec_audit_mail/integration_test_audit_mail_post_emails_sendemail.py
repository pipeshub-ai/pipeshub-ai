"""Strict OpenAPI audit of POST /api/v1/mail/emails/sendEmail.

A 200 means an email was handed to the SMTP server, so every success is read
back from Mailpit and every refusal is checked to have delivered nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests
from mail_audit_support import (
    FETCH_CONFIG_SCOPE,
    SEND_EMAIL_ROUTE,
    SEND_FAILED_CODE,
    SEND_FAILED_MESSAGE,
    SEND_MAIL_SCOPE,
    SENT_BODY,
    TEMPLATES,
    UNKNOWN_TEMPLATE_TYPE,
    WRONG_SECRET,
    MailClient,
    MintScopedToken,
    NewRecipient,
    addresses,
    delivered,
    email_body,
    mint_scoped_token,
    scoped_jwt_secret,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = [pytest.mark.spec_audit, pytest.mark.usefixtures("smtp_ready")]

ROUTE = SEND_EMAIL_ROUTE


def _assert_sent(resp: requests.Response) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SENT_BODY


def _assert_send_failed(resp: requests.Response) -> None:
    assert resp.status_code == 500, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == SEND_FAILED_CODE, error
    # The cause is only logged; every failure reads the same to the caller.
    assert error["message"].startswith(SEND_FAILED_MESSAGE), error


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_send_email_delivers_each_template(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    template: str,
    new_recipient: NewRecipient,
) -> None:
    recipient = new_recipient()
    body = email_body(template, to=[recipient])

    resp = mail_client.send_email(body, token=scoped_token(SEND_MAIL_SCOPE))

    _assert_sent(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    (message,) = delivered(recipient)
    assert message["Subject"] == body["subject"]
    assert addresses(message["To"]) == [recipient]
    data, shown_key = TEMPLATES[template]
    assert str(data[shown_key]) in message["HTML"], (template, shown_key)


def test_send_email_with_every_documented_field(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    pipeshub_client: PipeshubClient,
    new_recipient: NewRecipient,
) -> None:
    recipient, copied = new_recipient(), new_recipient()
    body = email_body(
        to=[recipient],
        sendCcTo=[copied],
        attachments=[{"filename": "spec-audit.txt", "content": "spec audit"}],
        orgId=pipeshub_client.org_id,
        productName="Spec Audit",
        isAutoEmail=True,
        fromEmailDomain="sender.example.invalid",
    )

    resp = mail_client.send_email(body, token=scoped_token(SEND_MAIL_SCOPE))

    _assert_sent(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    (message,) = delivered(recipient)
    assert addresses(message["To"]) == [recipient]
    assert addresses(message["Cc"]) == [copied]
    assert [a["FileName"] for a in message["Attachments"]] == ["spec-audit.txt"]
    # fromEmailDomain is accepted and unused: the sender is the configured fromEmail.
    assert not message["From"]["Address"].endswith("@sender.example.invalid"), message["From"]


def test_send_email_with_only_the_required_fields(
    mail_client: MailClient, scoped_token: MintScopedToken, new_recipient: NewRecipient
) -> None:
    recipient = new_recipient()
    body = {
        "emailTemplateType": "loginWithOTP",
        "sendEmailTo": [recipient],
        "templateData": {},
    }

    # The token names neither a user nor an org; the route reads only its scope.
    token = mint_scoped_token(scoped_jwt_secret(), [SEND_MAIL_SCOPE])
    resp = mail_client.send_email(body, token=token)

    _assert_sent(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    (message,) = delivered(recipient)
    assert message["Subject"] == ""


@pytest.mark.parametrize(
    ("field", "as_list", "mailpit_field"),
    [
        pytest.param("sendEmailTo", False, "to", id="to_as_one_string"),
        pytest.param("sendCcTo", True, "cc", id="cc_only"),
        pytest.param("sendCcTo", False, "cc", id="cc_only_as_one_string"),
    ],
)
def test_send_email_recipient_forms(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    field: str,
    as_list: bool,
    mailpit_field: str,
    new_recipient: NewRecipient,
) -> None:
    recipient = new_recipient()
    body = email_body(**{field: [recipient] if as_list else recipient})

    resp = mail_client.send_email(body, token=scoped_token(SEND_MAIL_SCOPE))

    _assert_sent(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    (message,) = delivered(recipient, field=mailpit_field)
    received = message["To"] if mailpit_field == "to" else message["Cc"]
    assert addresses(received) == [recipient]


@pytest.mark.parametrize(
    ("reason", "fields"),
    [
        pytest.param(
            "a field the route does not know is ignored",
            {"specAuditUnknownField": {"nested": True}},
            id="unknown_field",
        ),
        pytest.param(
            "subject is not type-checked; the mail library turns it into text",
            {"subject": 20261006},
            id="numeric_subject",
        ),
        pytest.param(
            "templateData is only checked for being truthy",
            {"templateData": "not-an-object"},
            id="string_template_data",
        ),
        pytest.param(
            "orgId is only used for the audit record, whose failed save is logged",
            {"orgId": "not-an-object-id"},
            id="org_id_not_an_object_id",
        ),
    ],
)
def test_send_email_tolerates_what_it_does_not_validate(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    reason: str,
    fields: dict[str, Any],
    new_recipient: NewRecipient,
) -> None:
    recipient = new_recipient()
    body = email_body(to=[recipient], **fields)

    with outside_request_contract(reason):
        resp = mail_client.send_email(body, token=scoped_token(SEND_MAIL_SCOPE))

    _assert_sent(resp)
    assert_strict_openapi_response(resp, ROUTE)
    assert len(delivered(recipient)) == 1


_NO_FILE = [{"filename": "hostname.txt", "path": "/etc/hostname"}]
_NO_URL = [{"filename": "page.html", "href": "http://127.0.0.1:8025/"}]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"emailTemplateType": UNKNOWN_TEMPLATE_TYPE}, id="unknown_template"),
        pytest.param({"emailTemplateType": None}, id="no_template_type"),
        pytest.param({"emailTemplateType": 7}, id="numeric_template_type"),
        pytest.param({"templateData": None}, id="no_template_data"),
        pytest.param({"templateData": False}, id="falsy_template_data"),
        pytest.param(
            {"emailTemplateType": "emailChangeNotice", "templateData": {"name": "x"}},
            id="email_change_notice_missing_data",
        ),
        pytest.param({"sendEmailTo": None}, id="no_recipient"),
        pytest.param({"sendEmailTo": []}, id="empty_recipient_list"),
        pytest.param({"sendEmailTo": ["not-an-address"]}, id="unusable_address"),
        pytest.param({"attachments": _NO_FILE}, id="attachment_from_local_file"),
        pytest.param({"attachments": _NO_URL}, id="attachment_from_url"),
        pytest.param({"attachments": "not-a-list"}, id="attachments_not_a_list"),
    ],
)
def test_send_email_that_cannot_be_sent_is_an_internal_error(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    change: dict[str, Any],
    new_recipient: NewRecipient,
) -> None:
    recipient = new_recipient()
    body = email_body(to=[recipient])
    for key, value in change.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value

    resp = mail_client.send_email(body, token=scoped_token(SEND_MAIL_SCOPE))

    # API bug: each of these is a caller mistake, yet none answers 400.
    _assert_send_failed(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert delivered(recipient, wait_seconds=0) == []


@pytest.mark.parametrize("body", [{}, None], ids=["empty_object", "no_body"])
def test_send_email_without_a_usable_body_is_an_internal_error(
    mail_client: MailClient, scoped_token: MintScopedToken, body: Any
) -> None:
    resp = mail_client.send_email(body, token=scoped_token(SEND_MAIL_SCOPE))

    _assert_send_failed(resp)
    assert_strict_openapi_exchange(resp, ROUTE)


def _token_for(kind: str, scoped_token: MintScopedToken) -> str | None:
    secret = scoped_jwt_secret()
    return {
        "wrong_secret": lambda: mint_scoped_token(WRONG_SECRET, [SEND_MAIL_SCOPE]),
        "expired": lambda: scoped_token(SEND_MAIL_SCOPE, ttl_seconds=-60),
        "other_scope": lambda: scoped_token(FETCH_CONFIG_SCOPE),
        "no_scopes_claim": lambda: mint_scoped_token(secret, None),
        "empty_scopes": lambda: mint_scoped_token(secret, []),
    }.get(kind, lambda: None)()


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("no_token", "No token provided"),
        ("basic_scheme", "No token provided"),
        ("admin_session", "Invalid token"),
        ("wrong_secret", "Invalid token"),
        ("expired", "Invalid token"),
        ("other_scope", "Invalid scope"),
        ("no_scopes_claim", "Invalid scope"),
        ("empty_scopes", "Invalid scope"),
    ],
)
def test_send_email_without_a_mail_send_token_is_unauthorized(
    mail_client: MailClient,
    scoped_token: MintScopedToken,
    kind: str,
    message: str,
    new_recipient: NewRecipient,
) -> None:
    recipient = new_recipient()
    body = email_body(to=[recipient])

    if kind == "no_token":
        resp = mail_client.send_email(body, auth=False)
    elif kind == "basic_scheme":
        resp = mail_client.send_email(
            body, auth=False, headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
    elif kind == "admin_session":
        resp = mail_client.send_email(body, auth=True)
    else:
        resp = mail_client.send_email(body, token=_token_for(kind, scoped_token))

    assert resp.status_code == 401, resp.text[:500]
    assert resp.json()["error"]["message"] == message
    assert_strict_openapi_exchange(resp, ROUTE)
    assert delivered(recipient, wait_seconds=0) == []


def test_send_email_accepts_a_string_scopes_claim_that_contains_the_scope(
    mail_client: MailClient, new_recipient: NewRecipient
) -> None:
    # API bug: the check is scopes.includes(scope), which on a string is a substring test,
    # so this token passes although it holds no "mail:send" scope.
    token = mint_scoped_token(scoped_jwt_secret(), f"not-{SEND_MAIL_SCOPE}-at-all")
    recipient = new_recipient()

    resp = mail_client.send_email(email_body(to=[recipient]), token=token)

    _assert_sent(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert len(delivered(recipient)) == 1
