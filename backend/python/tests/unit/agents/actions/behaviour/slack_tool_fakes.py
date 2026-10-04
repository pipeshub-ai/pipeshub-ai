"""Fakes for behaviour tests of the Slack agent tools.

Only the HTTP layer is faked. The tool runs through the real ``SlackDataSource``
and a real ``slack_sdk.WebClient`` holding a user token; the fake replaces the
client's lowest urllib call, so argument encoding, the Authorization header,
``ok: false`` handling, ``SlackApiError`` and 429 handling are all the SDK's own.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from email.message import Message
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from app.agents.actions.slack.slack import Slack
from app.sources.client.slack.slack import SlackClient, SlackRESTClientViaToken

if TYPE_CHECKING:
    from urllib.request import Request

USER_TOKEN = "xoxp-1111-2222-fake-user-token-must-never-leak"
ME = "U0MEAAAAAA"


@dataclass
class SlackCall:
    method: str
    args: dict[str, Any]
    headers: dict[str, str]


@dataclass
class SlackReply:
    payload: dict[str, Any]
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


def slack_error(code: str, status: int = 200, headers: dict[str, str] | None = None, **extra: object) -> SlackReply:
    """Slack reports most failures as HTTP 200 with ``ok: false``; rate limits come as 429."""
    return SlackReply({"ok": False, "error": code, **extra}, status, headers or {})


def rate_limited(retry_after: int = 30) -> SlackReply:
    return slack_error("ratelimited", status=429, headers={"Retry-After": str(retry_after)})


class FakeSlackApi:
    """Routes by Web API method name (``chat.postMessage``); records every call.

    A route's replies are consumed one per call and the last repeats, which is
    how pagination and "fail, then succeed" are staged. A reply may also be a
    callable taking the call's arguments. Unrouted methods answer
    ``unknown_method`` and are kept in ``unrouted``.
    """

    def __init__(self) -> None:
        self.routes: dict[str, list[object]] = {}
        self.calls: list[SlackCall] = []
        self.unrouted: list[str] = []

    def on(self, method: str, *replies: object) -> "FakeSlackApi":
        self.routes[method] = list(replies)
        return self

    def called(self, method: str) -> list[SlackCall]:
        return [c for c in self.calls if c.method == method]

    def methods(self) -> list[str]:
        return [c.method for c in self.calls]

    def __call__(self, url: str, req: Request) -> dict[str, Any]:
        parsed = urlparse(url)
        method = parsed.path.rsplit("/", 1)[-1]
        args: dict[str, Any] = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        body = req.data.decode() if isinstance(req.data, bytes) else ""
        if body:
            try:
                args.update(json.loads(body))
            except json.JSONDecodeError:
                args.update({k: v[0] for k, v in parse_qs(body).items()})
        call = SlackCall(method, args, {k.lower(): v for k, v in req.header_items()})
        self.calls.append(call)

        replies = self.routes.get(method)
        if replies is None:
            self.unrouted.append(method)
            reply: object = slack_error("unknown_method")
        else:
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if callable(reply):
            reply = reply(args)
        if not isinstance(reply, SlackReply):
            reply = SlackReply({"ok": True, **reply})  # type: ignore[dict-item]
        body_text = json.dumps(reply.payload)
        headers = {"Content-Type": "application/json; charset=utf-8", **reply.headers}
        if reply.status >= 400:
            message = Message()
            for key, value in headers.items():
                message[key] = value
            raise HTTPError(url, reply.status, "error", message, io.BytesIO(body_text.encode()))
        return {"status": reply.status, "headers": headers, "body": body_text}


def build_slack_tool(api: FakeSlackApi, state: object = None) -> Slack:
    """The tool as the agent factory builds it, on a real WebClient whose HTTP is ``api``."""
    rest = SlackRESTClientViaToken(USER_TOKEN)
    rest.get_web_client()._perform_urllib_http_request_internal = api  # type: ignore[method-assign]
    return Slack(SlackClient(rest), state=state)


def result(outcome: tuple[bool, str]) -> tuple[bool, dict[str, Any]]:
    success, text = outcome
    return success, json.loads(text)


def user(user_id: str, name: str, email: str | None = None, **extra: object) -> dict[str, Any]:
    profile = {"display_name": name, "real_name": name, **({"email": email} if email else {})}
    return {"id": user_id, "name": name.lower(), "real_name": name, "profile": profile, **extra}
