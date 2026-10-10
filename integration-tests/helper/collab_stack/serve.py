"""Serve mode of the lane for the browser (Playwright) tests.

Boots the same stack as the pytest lane (Mongo + Redis containers, the real Node API, the fake of the Python
services), turns the collaborative-chats and mentions flags on, then keeps running until it is stopped. Two things are published:

* a state file (``--state-file``): Node URL, control URL and the roster with a session JWT per actor;
* a small JSON control API on 127.0.0.1 so a browser test, which cannot import the fake, can script it.

Run from ``integration-tests``: ``python -m helper.collab_stack.serve --state-file /path/state.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from helper.collab_stack.fake_backend import (
    Reply,
    Sse,
    ai_answer,
    agent_draft_stream,
    ask_user_question,
    held_stream,
    run_error,
    stream_answer,
)
from helper.collab_stack import collab
from helper.collab_stack.pdp import AUTHZ_CHECK_SCOPE, service_token
from helper.collab_stack.flags import AGENT_BUILDER_FLAG, COLLAB_FLAG, MENTIONS_FLAG
from helper.collab_stack.identity import Directory
from helper.collab_stack.seeds import insert_session, team_row, user_row
from helper.collab_stack.stack import CollabStack, install_signal_cleanup

logger = logging.getLogger("pcc-e2e-serve")

TOKEN_TTL_S = 8 * 3600


def seed_ai_models(stack: CollabStack) -> None:
    """The chat UI will not send without a configured model. The AI backend's health check is the fake's, so any config is accepted."""
    admin = stack.roster.admin  # type: ignore[union-attr]
    for model_type in ("llm", "embedding"):
        resp = stack.api.post(  # type: ignore[union-attr]
            "/api/v1/configurationManager/ai-models/providers",
            admin,
            json_body={
                "modelType": model_type,
                "provider": "openAICompatible",
                "configuration": {"endpoint": stack.fake.url, "apiKey": "unused", "model": f"fake-{model_type}"},
                "isMultimodal": False,
                "isReasoning": False,
                "isDefault": True,
                "contextLength": None,
            },
        )
        assert resp.status_code in (200, 201), f"seeding the {model_type} model: {resp.status_code} {resp.text[:300]}"


def build_reply(spec: dict[str, Any], stack: CollabStack) -> Reply | Sse:
    """A scripted reply from its JSON description: ``kind`` is one of held_stream, ask_user_question, agent_draft, stream_answer, run_error, reply."""
    kind = spec["kind"]
    run_id = spec.get("runId", "fake-run")
    if kind == "held_stream":
        return held_stream(stack.fake.gate(spec["gate"]), spec.get("text", "Fake answer"), run_id=run_id)
    if kind == "ask_user_question":
        return ask_user_question(spec.get("toolData"), run_id=run_id)
    if kind == "agent_draft":
        return agent_draft_stream(spec["draft"], spec.get("text", "I drafted it for you to review."), run_id=run_id)
    if kind == "stream_answer":
        over = {"answerMatchType": spec["answerMatchType"]} if "answerMatchType" in spec else {}
        return stream_answer(spec.get("text", "Fake answer"), run_id=run_id, **over)
    if kind == "run_error":
        return run_error(spec.get("message", "boom"))
    if kind == "reply":
        return Reply(spec.get("body", ai_answer(spec.get("text", "Fake answer"))), status=spec.get("status", 200))
    raise ValueError(f"unknown reply kind {kind!r}")


def actor_json(actor: Any) -> dict[str, Any]:
    return {
        "name": actor.name,
        "userId": actor.user_id,
        "orgId": actor.org_id,
        "email": actor.email,
        "role": actor.role,
        "token": Directory.session_token(actor, TOKEN_TTL_S),
    }


class Control:
    """The routes of the control API, kept apart from the HTTP plumbing so they read as a table."""

    def __init__(self, stack: CollabStack, stop: threading.Event) -> None:
        self.stack = stack
        self.stop = stop

    def state(self) -> dict[str, Any]:
        stack = self.stack
        assert stack.node and stack.roster
        roster = {key: actor_json(actor) for key, actor in vars(stack.roster).items()}
        return {"nodeUrl": stack.node.base_url, "fakeUrl": stack.fake.url, "roster": roster}

    def handle(self, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]) -> Any:
        parts = [p for p in path.split("/") if p]
        if method == "GET" and parts == ["health"]:
            return {"ok": True}
        if method == "GET" and parts == ["state"]:
            return self.state()
        if method == "POST" and parts == ["reset"]:
            self.stack.reset_state()
            return {"ok": True}
        if method == "POST" and parts == ["script"]:
            replies = [build_reply(spec, self.stack) for spec in body["replies"]]
            self.stack.fake.on(body["route"], *replies)
            return {"queued": len(replies)}
        if method == "POST" and parts == ["team"]:
            actors = vars(self.stack.roster)  # type: ignore[arg-type]
            # A key is a roster key, or a raw user id (a fresh actor made through /actor).
            members = {(actors[k].user_id if k in actors else k): role for k, role in body.get("members", {}).items()}
            org_id = self.stack.roster.owner.org_id  # type: ignore[union-attr]
            team = self.stack.fake.add_team(org_id, body["name"], members, body.get("teamId"))
            return {"teamId": team.team_id}
        if method == "POST" and parts == ["actor"]:
            # A user of their own: sharing is rate limited per sharer and the owner status is remembered per user for 60 s.
            actor = collab.fresh_actor(self.stack, body.get("label", "E2E"), disabled=bool(body.get("disabled", False)))
            return actor_json(actor)
        if method == "POST" and parts == ["user", "disabled"]:
            actor = self.stack.directory.actors_by_id(body["userId"])  # type: ignore[union-attr]
            self.stack.directory.set_disabled(actor, bool(body["disabled"]))  # type: ignore[union-attr]
            return {"disabled": bool(body["disabled"])}
        if method == "POST" and parts == ["team", "member"]:
            # A membership change made outside Node: only the connectors fake is told.
            team = self.stack.fake.teams[body["teamId"]]
            if body.get("remove"):
                team.members.pop(body["userId"], None)
            else:
                team.members[body["userId"]] = body.get("role", "READER")
            return {"members": len(team.members)}
        if method == "GET" and parts == ["cache"]:
            # The decision and team-id cache entries of a user, with their remaining TTL.
            redis = self.stack.infra.redis
            out = {}
            for pattern in query.get("pattern", ["*authz:v1:*", "*teamids:*"]):
                for key in redis.scan_iter(match=pattern, count=500):
                    out[key] = redis.ttl(key)
            return {"keys": out}
        if method == "POST" and parts == ["cache", "delete"]:
            redis = self.stack.infra.redis
            gone = 0
            for pattern in body["patterns"]:
                for key in list(redis.scan_iter(match=pattern, count=500)):
                    gone += redis.delete(key)
            return {"deleted": gone}
        if method == "POST" and parts == ["service-token"]:
            return {"token": service_token(body["orgId"], tuple(body.get("scopes", [AUTHZ_CHECK_SCOPE])))}
        if method == "POST" and parts == ["flag"]:
            self.stack.flags.set(bool(body["enabled"]), body.get("key", COLLAB_FLAG))  # type: ignore[union-attr]
            return {"enabled": bool(body["enabled"])}
        if method == "POST" and parts == ["seed-chat"]:
            # A chat the API cannot make on purpose: shared, with no messages yet (the shared empty state).
            actors = vars(self.stack.roster)
            shared = [user_row(actors[k], level, principal_type=True) for k, level in body.get("users", {}).items()]
            shared += [team_row(team_id, level) for team_id, level in body.get("teams", {}).items()]
            seeded = insert_session(
                self.stack.db, body["key"], actors[body.get("owner", "owner")], kind=body.get("kind", "chat"),
                shared_with=shared, messages=[] if body.get("empty") else None,
            )
            return {"sessionId": str(seeded.sid)}
        if method == "POST" and parts == ["tips", "reset"]:
            # Coachmarks show once per user; a screenshot tour needs them again for every viewport and theme.
            self.stack.db["userNotificationPreferences"].update_many({}, {"$set": {"tipsSeen": []}})
            return {"ok": True}
        if len(parts) == 3 and parts[0] == "gate":
            gate = self.stack.fake.gate(parts[1])
            if method == "POST" and parts[2] == "open":
                gate.open()
                return {"open": True}
            if method == "GET" and parts[2] == "reached":
                return {"reached": gate.reached.is_set()}
        if method == "GET" and parts == ["mark"]:
            return {"mark": self.stack.fake.mark()}
        if method == "GET" and parts == ["requests"]:
            routes = [r for r in query.get("route", [""])[0].split(",") if r]
            since = int(query.get("since", ["0"])[0])
            return {
                "requests": [
                    {"route": r.route, "method": r.method, "path": r.path, "userId": r.user_id, "body": r.body, "at": r.at}
                    for r in self.stack.fake.since(since, *routes)
                ]
            }
        if method == "POST" and parts == ["shutdown"]:
            self.stop.set()
            return {"ok": True}
        raise LookupError(f"{method} {path}")


def make_handler(control: Control) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self, method: str) -> None:
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else {}
            try:
                status, out = 200, control.handle(method, url.path, parse_qs(url.query), body)
            except LookupError as exc:
                status, out = 404, {"error": f"no control route {exc}"}
            except Exception as exc:  # noqa: BLE001 - reported to the caller, which fails its test
                logger.exception("control call failed")
                status, out = 500, {"error": f"{type(exc).__name__}: {exc}"}
            payload = json.dumps(out).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--frontend-origin", default="http://localhost:3001", help="the origin the Node API allows (CORS) and links back to")
    parser.add_argument("--flag", choices=("on", "off"), default="on")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    stack = CollabStack(node_env={
            "ALLOWED_ORIGINS": args.frontend_origin,
            "FRONTEND_PUBLIC_URL": args.frontend_origin,
            # The health gate of the UI probes these; the fake answers all of them healthy.
            "DOCLING_BACKEND": "{fake_url}",
            "EMBEDDING_SERVER_URL": "{fake_url}",
        })
    install_signal_cleanup(stack)
    stop = threading.Event()
    signal.signal(signal.SIGUSR1, lambda *_: stop.set())
    server: ThreadingHTTPServer | None = None
    try:
        stack.start()
        seed_ai_models(stack)
        if args.flag == "on":
            stack.flags.set(True)  # type: ignore[union-attr]
            # The composer swaps to the rich editor on this flag; the UI reads it from the effective-flags route.
            stack.flags.set(True, key=MENTIONS_FLAG)  # type: ignore[union-attr]
            stack.flags.set(True, key=AGENT_BUILDER_FLAG)  # type: ignore[union-attr]
        control = Control(stack, stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(control))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        state = {**control.state(), "controlUrl": f"http://127.0.0.1:{server.server_address[1]}"}
        tmp = args.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(args.state_file)
        print("PCC_E2E_READY " + json.dumps({k: state[k] for k in ("nodeUrl", "controlUrl")}), flush=True)
        stop.wait()
    finally:
        if server:
            server.shutdown()
        stack.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
