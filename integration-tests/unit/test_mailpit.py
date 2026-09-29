"""The sign-in code is read from its own element, never from names around it."""

from __future__ import annotations

from pathlib import Path

import pytest

from helper.mailpit import Message, reset_link_token, sign_in_code

pytestmark = pytest.mark.unit

_VIEWS = (
    Path(__file__).resolve().parents[2]
    / "backend/nodejs/apps/src/modules/mail/views/layouts/user"
)


def _render(template: str, **values: str) -> str:
    html = (_VIEWS / template).read_text()
    for key, value in values.items():
        html = html.replace("{{{" + key + "}}}", value).replace("{{" + key + "}}", value)
    return html


def test_the_code_is_found_when_the_name_and_org_contain_six_digits() -> None:
    html = _render(
        "login.hbs",
        name="IT Second User 1234567a",
        orgName="Acme 998877",
        otp="042917",
    )
    assert sign_in_code(Message("1", "OTP for Login", html, "")) == "042917"


def test_a_text_part_code_on_its_own_line_is_found() -> None:
    text = "Hi IT Second User 123456ab,\n\nYour code:\n  314159\n"
    assert sign_in_code(Message("1", "OTP for Login", "", text)) == "314159"


def test_an_email_without_a_code_is_reported() -> None:
    html = _render("login.hbs", name="IT Second User 123456ab", otp="")
    with pytest.raises(AssertionError, match="found 0"):
        sign_in_code(Message("1", "OTP for Login", html, ""))


def test_the_reset_link_token_is_read_from_the_link() -> None:
    html = _render(
        "resetPassword.hbs",
        name="IT Second User 12345678",
        link="http://localhost:3000/reset-password#token=aaa.bbb-c_d.eee",
    )
    assert reset_link_token(Message("2", "Reset", html, "")) == "aaa.bbb-c_d.eee"
