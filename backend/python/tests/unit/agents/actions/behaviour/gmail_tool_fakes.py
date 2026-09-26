"""Fakes for behaviour tests of the Gmail agent tools.

Reuses the Google HTTP fake from the Calendar tests: the tool runs through the
real ``GoogleGmailDataSource`` and a real Gmail v1 ``googleapiclient`` Resource
on real ``AuthorizedHttp`` credentials, so URLs, query strings, bodies, the
library's own retries and ``HttpError`` parsing are all real.
"""

from __future__ import annotations

import base64
import email
from typing import TYPE_CHECKING, Any

from gcal_tool_fakes import ACCESS_TOKEN, CLIENT_SECRET, REFRESH_TOKEN, FakeGoogleHttp
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.agents.actions.google.gmail.gmail import Gmail

if TYPE_CHECKING:
    from email.message import Message

GMAIL = "/gmail/v1"
MESSAGES = "/users/me/messages"
SEND = "/users/me/messages/send"
DRAFTS = "/users/me/drafts"


def build_gmail_tool(http: FakeGoogleHttp, *, refreshable: bool = False, state: object = None) -> Gmail:
    """The tool as the agent factory builds it: a Gmail v1 Resource on authorized HTTP."""
    credentials = Credentials(token=ACCESS_TOKEN)
    if refreshable:
        credentials = Credentials(
            token=ACCESS_TOKEN, refresh_token=REFRESH_TOKEN, client_id="client-id",
            client_secret=CLIENT_SECRET, token_uri="https://oauth2.googleapis.com/token",
        )
    service = build("gmail", "v1", http=AuthorizedHttp(credentials, http=http), static_discovery=True, cache_discovery=False)
    return Gmail(service, state=state)


def sent_mime(body: dict[str, Any]) -> Message:
    """The MIME message inside a users.messages.send / drafts.create body."""
    raw = body["raw"] if "raw" in body else body["message"]["raw"]
    return email.message_from_bytes(base64.urlsafe_b64decode(raw))


def headers(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"name": name, "value": value} for name, value in pairs]


def attachment_part(filename: str, attachment_id: str, *, mime: str = "application/pdf", size: int = 10) -> dict[str, Any]:
    return {"filename": filename, "mimeType": mime, "body": {"attachmentId": attachment_id, "size": size}}
