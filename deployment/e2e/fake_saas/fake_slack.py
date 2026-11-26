"""R35-SLACK: a scripted Slack workspace for the e2e lanes: the Web API the PipesHub Slack bot (and Node's own Slack
notices) call, and signed event deliveries to the bot, as Slack sends them.

    /slack/api/{method}                    the Web API (``SLACK_API_URL`` points the bot's and Node's clients here)
    PUT  /__control/slack/users            the workspace's people: ``{"U…": {"email": …, "name": …}}``, optionally with
                                           ``team`` (another workspace's person, as Slack Connect shows one) and Slack's
                                           account flags (``is_restricted``, ``is_ultra_restricted``, ``is_stranger``,
                                           ``deleted``, ``is_bot``)
    POST /__control/slack/deliver          an event delivered to the bot, signed with the bot's signing secret
    GET  /__control/slack/thread           a thread's messages (``channel``, ``ts``), as a person in Slack reads them
    GET  /__control/slack/calls            every Web API call made (method, channel, thread), newest last
    GET  /__control/slack/ephemeral        the messages only one person saw (``chat.postEphemeral``), ``?user=U…``

A bot token names its bot: ``auth.test`` answers ``U<token's last part>``. Messages keep their text, blocks and a
streamed answer's markdown; a deleted one stays listed, marked deleted, so a test reads what the person saw last. The
bot is reached at ``SLACK_BOT_EVENTS_URL`` (the app container's Slack bot). Control routes need ``X-E2E-Control``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse

BOT_EVENTS_URL = os.environ.get("SLACK_BOT_EVENTS_URL", "http://pipeshub-ai:3020/slack/events")
TEAM = "T0E2E"
USERS: dict[str, dict[str, Any]] = {}
MESSAGES: list[dict[str, Any]] = []
EPHEMERAL: list[dict[str, Any]] = []
FLAGS = ("is_restricted", "is_ultra_restricted", "is_stranger", "deleted", "is_bot")
CALLS: list[dict[str, Any]] = []
_SEQ = itertools.count(1)
Json = dict[str, Any]


def _ts() -> str:
    return f"{int(time.time())}.{next(_SEQ):06d}"


def _bot_user(token: str) -> str:
    return "U" + (token.rsplit("-", 1)[-1] or "BOT").upper()[:10]


async def _args(request: Request) -> Json:
    """A Web API call's arguments: form-encoded (the SDK's default; its arrays and objects JSON strings) or JSON."""
    raw = await request.body()
    if "application/json" in request.headers.get("content-type", ""):
        body = json.loads(raw or b"{}")
    else:
        body = {k: v[-1] for k, v in parse_qs(raw.decode()).items()}
    out: Json = {}
    for key, value in body.items():
        if key in ("blocks", "attachments", "chunks") and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        out[key] = value
    return out


def _token(request: Request, args: Json) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else str(args.get("token") or "")


def _find(channel: str, ts: str) -> Json | None:
    return next((m for m in MESSAGES if m["channel"] == channel and m["ts"] == ts), None)


def _post(channel: str, args: Json, bot: str, *, streaming: bool = False) -> Json:
    message = {"channel": channel, "ts": _ts(), "thread_ts": str(args.get("thread_ts") or "") or None,
               "user": bot, "bot_id": "B" + bot[1:], "text": str(args.get("text") or ""),
               "blocks": args.get("blocks") or [], "markdown": str(args.get("markdown_text") or ""),
               "streaming": streaming, "deleted": False, "at": time.time()}
    MESSAGES.append(message)
    return message


def _public(message: Json) -> Json:
    return {k: message[k] for k in ("ts", "thread_ts", "user", "bot_id", "text", "blocks")} | {
        "type": "message", "channel": message["channel"]}


async def web_api(request: Request) -> JSONResponse:
    method = request.path_params["method"]
    args = await _args(request)
    token = _token(request, args)
    if not token.startswith("xoxb-"):
        return JSONResponse({"ok": False, "error": "not_authed"})
    bot = _bot_user(token)
    channel = str(args.get("channel") or "")
    CALLS.append({"method": method, "channel": channel, "thread_ts": args.get("thread_ts"), "ts": args.get("ts"),
                  "bot": bot, "at": time.time()})
    if method == "auth.test":
        return JSONResponse({"ok": True, "user_id": bot, "bot_id": "B" + bot[1:], "team_id": TEAM, "user": "pipeshub"})
    if method == "users.info":
        user = str(args.get("user") or "")
        person = USERS.get(user)
        if person is None:
            return JSONResponse({"ok": False, "error": "user_not_found"})
        return JSONResponse({"ok": True, "user": {"id": user, "name": person["name"], "real_name": person["name"],
                                                  "team_id": person.get("team") or TEAM,
                                                  **{flag: True for flag in FLAGS if person.get(flag)},
                                                  "tz": "UTC", "profile": {"email": person["email"],
                                                                           "real_name": person["name"],
                                                                           "display_name": person["name"]}}})
    if method == "users.lookupByEmail":
        email = str(args.get("email") or "")
        user = next((u for u, p in USERS.items() if p["email"] == email), None)
        return JSONResponse({"ok": True, "user": {"id": user}} if user else {"ok": False, "error": "users_not_found"})
    if method == "chat.postMessage":
        message = _post(channel, args, bot)
        return JSONResponse({"ok": True, "channel": channel, "ts": message["ts"], "message": _public(message)})
    if method == "chat.postEphemeral":
        EPHEMERAL.append({"channel": channel, "user": str(args.get("user") or ""), "text": str(args.get("text") or ""),
                          "thread_ts": args.get("thread_ts"), "bot": bot, "at": time.time()})
        return JSONResponse({"ok": True, "message_ts": _ts()})
    if method == "chat.startStream":
        message = _post(channel, args, bot, streaming=True)
        return JSONResponse({"ok": True, "channel": channel, "ts": message["ts"]})
    if method in ("chat.update", "chat.appendStream", "chat.stopStream", "chat.delete"):
        message = _find(channel, str(args.get("ts") or ""))
        if message is None or message["deleted"]:
            return JSONResponse({"ok": False, "error": "message_not_found"})
        if method == "chat.delete":
            message["deleted"] = True
        elif method == "chat.update":
            message.update(text=str(args.get("text") or ""), blocks=args.get("blocks") or [], markdown="")
        elif not message["streaming"]:
            return JSONResponse({"ok": False, "error": "message_not_in_streaming_state"})
        else:
            message["markdown"] += str(args.get("markdown_text") or "")
            if method == "chat.stopStream":
                message["streaming"] = False
                if args.get("blocks"):
                    message["blocks"] = args["blocks"]
        return JSONResponse({"ok": True, "channel": channel, "ts": message["ts"]})
    if method == "conversations.replies":
        ts = str(args.get("ts") or "")
        found = [_public(m) for m in MESSAGES if m["channel"] == channel and not m["deleted"]
                 and (m["ts"] == ts or m["thread_ts"] == ts)]
        return JSONResponse({"ok": True, "messages": found, "has_more": False})
    return JSONResponse({"ok": True})


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return f"v0={digest}"


async def deliver(request: Request) -> JSONResponse:
    """A person's message delivered to the bot: ``{"signingSecret", "botToken", "user", "text", "channel"?,
    "thread_ts"?}``. A channel ``C…`` is an ``app_mention`` (the text opens with the bot's mention); ``D…`` a direct
    message. The person's message is kept in the thread as Slack keeps it. A person of another workspace (``team`` in
    ``/__control/slack/users``) arrives as Slack Connect delivers them: ``user_team`` and ``source_team`` name their
    workspace; ``"shared": true`` marks the channel as shared with another organisation."""
    body = await request.json()
    bot = _bot_user(str(body["botToken"]))
    channel = str(body.get("channel") or "C0E2E")
    user = str(body["user"])
    mention = channel.startswith("C")
    text = f"<@{bot}> {body['text']}" if mention else str(body["text"])
    ts = _ts()
    thread_ts = str(body.get("thread_ts") or "") or None
    MESSAGES.append({"channel": channel, "ts": ts, "thread_ts": thread_ts, "user": user, "bot_id": None, "text": text,
                     "blocks": [], "markdown": "", "streaming": False, "deleted": False, "at": time.time()})
    team = str((USERS.get(user) or {}).get("team") or TEAM)
    event: Json = {"type": "app_mention" if mention else "message", "user": user, "text": text, "ts": ts,
                   "channel": channel, "event_ts": ts, "team": team}
    if team != TEAM:
        event |= {"user_team": team, "source_team": team}
    if not mention:
        event["channel_type"] = "im"
    if thread_ts:
        event["thread_ts"] = thread_ts
    envelope = {"token": "unused", "team_id": TEAM, "api_app_id": "A0E2E", "type": "event_callback",
                "event_id": f"Ev{ts.replace('.', '')}", "event_time": int(time.time()), "event": event,
                "authorizations": [{"team_id": TEAM, "user_id": bot, "is_bot": True}]}
    if body.get("shared"):
        envelope["is_ext_shared_channel"] = True
    raw = json.dumps(envelope).encode()
    stamp = str(int(time.time()))
    forwarded = urllib.request.Request(BOT_EVENTS_URL, data=raw, method="POST", headers={
        "Content-Type": "application/json", "X-Slack-Request-Timestamp": stamp,
        "X-Slack-Signature": _sign(str(body["signingSecret"]), stamp, raw)})
    def send() -> int:
        try:
            with urllib.request.urlopen(forwarded, timeout=30) as answer:
                return int(answer.status)
        except urllib.error.HTTPError as error:
            return int(error.code)

    status = await asyncio.to_thread(send)
    return JSONResponse({"status": status, "channel": channel, "ts": ts, "thread_ts": thread_ts or ts})


async def users(request: Request) -> JSONResponse:
    for user, person in (await request.json()).items():
        USERS[str(user)] = {"email": str(person["email"]), "name": str(person.get("name") or user),
                            **({"team": str(person["team"])} if person.get("team") else {}),
                            **{flag: True for flag in FLAGS if person.get(flag)}}
    return JSONResponse({"users": len(USERS)})


async def thread(request: Request) -> JSONResponse:
    channel, ts = request.query_params.get("channel", ""), request.query_params.get("ts", "")
    found = [m for m in MESSAGES if m["channel"] == channel and (m["ts"] == ts or m["thread_ts"] == ts)]
    return JSONResponse({"messages": found})


async def calls(_request: Request) -> JSONResponse:
    return JSONResponse({"calls": CALLS})


async def ephemeral(request: Request) -> JSONResponse:
    user = request.query_params.get("user", "")
    return JSONResponse({"messages": [m for m in EPHEMERAL if not user or m["user"] == user]})


def register(mcp: Any, authorized: Any) -> None:
    """The Web API on ``/slack/api/{method}`` and the control routes, on the fake SaaS's app."""

    def controlled(handler: Any) -> Any:
        async def guarded(request: Request) -> JSONResponse:
            if not authorized(request):
                return JSONResponse({"error": "control token required"}, status_code=401)
            return await handler(request)
        return guarded

    mcp.custom_route("/slack/api/{method}", methods=["GET", "POST"])(web_api)
    mcp.custom_route("/__control/slack/users", methods=["PUT"])(controlled(users))
    mcp.custom_route("/__control/slack/deliver", methods=["POST"])(controlled(deliver))
    mcp.custom_route("/__control/slack/thread", methods=["GET"])(controlled(thread))
    mcp.custom_route("/__control/slack/calls", methods=["GET"])(controlled(calls))
    mcp.custom_route("/__control/slack/ephemeral", methods=["GET"])(controlled(ephemeral))
