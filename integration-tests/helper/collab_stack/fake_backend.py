"""Scriptable stand-in for the Python services the Node API calls.

One aiohttp server on a background thread plays the AI backend (``/api/v1/chat``, SSE streams,
agent chat), the connectors service (teams, KB) and the indexing service. Every request is recorded.
A test scripts the next reply for a named route, holds a stream open on a gate, or fails a route
for as long as a ``with fake.failing(...)`` block lasts.

Route names (the first column of ``ROUTES``) are what ``on``/``default``/``requests_for`` take.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import jwt
from aiohttp import ClientConnectionResetError, web

from helper.collab_stack import fake_llm

# (name, method, path regex). First match wins.
ROUTES: list[tuple[str, str, re.Pattern[str]]] = [
    ("health", "GET", re.compile(r"^/health$")),
    ("llm_chat", "POST", re.compile(r"^/v1/chat/completions$")),
    ("llm_embeddings", "POST", re.compile(r"^/v1/embeddings$")),
    ("llm_models", "GET", re.compile(r"^/v1/models$")),
    ("model_health_check", "POST", re.compile(r"^/api/v1/(health-check/[^/]+|llm-health-check|embedding-health-check)$")),
    ("connectors_list", "GET", re.compile(r"^/api/v1/connectors/?$")),
    ("demo_data_status", "GET", re.compile(r"^/api/v1/demo-data/status$")),
    ("my_toolsets", "GET", re.compile(r"^/api/v1/toolsets/my-toolsets$")),
    ("chat_stream", "POST", re.compile(r"^/api/v1/chat/stream$")),
    ("chat", "POST", re.compile(r"^/api/v1/chat$")),
    ("chat_cancel", "POST", re.compile(r"^/api/v1/chat/cancel$")),
    ("chat_cancel_participant", "POST", re.compile(r"^/api/v1/chat/cancel/participant$")),
    ("attachments_validate", "POST", re.compile(r"^/api/v1/chat/attachments/validate$")),
    ("agent_chat_stream", "POST", re.compile(r"^/api/v1/agent/(?P<agent_key>[^/]+)/chat/stream$")),
    ("agent_chat", "POST", re.compile(r"^/api/v1/agent/(?P<agent_key>[^/]+)/chat$")),
    ("agent_create_from_chat", "POST", re.compile(r"^/api/v1/agent/internal/create-from-chat$")),
    ("agent_handle_availability", "GET", re.compile(r"^/api/v1/agent/handle-availability$")),
    ("agent_create", "POST", re.compile(r"^/api/v1/agent/create$")),
    ("agent_list", "GET", re.compile(r"^/api/v1/agent/?$")),
    ("agent_readiness", "GET", re.compile(r"^/api/v1/agent/(?P<agent_key>[^/]+)/readiness$")),
    ("agent_item", "ANY", re.compile(r"^/api/v1/agent/(?P<agent_key>[^/]+)$")),
    ("agent_service_account", "GET", re.compile(r"^/api/v1/agent/(?P<agent_key>[^/]+)/internal/service-account$")),
    ("team_ids", "GET", re.compile(r"^/api/v1/entity/user/team-ids$")),
    ("user_teams", "GET", re.compile(r"^/api/v1/entity/user/teams$")),
    ("team_create", "POST", re.compile(r"^/api/v1/entity/team$")),
    ("team_users", "GET", re.compile(r"^/api/v1/entity/team/(?P<team_id>[^/]+)/users$")),
    ("team_get", "GET", re.compile(r"^/api/v1/entity/team/(?P<team_id>[^/]+)$")),
    ("team_update", "PUT", re.compile(r"^/api/v1/entity/team/(?P<team_id>[^/]+)$")),
    ("team_delete", "DELETE", re.compile(r"^/api/v1/entity/team/(?P<team_id>[^/]+)$")),
    ("kb_create", "POST", re.compile(r"^/api/v1/kb/?$")),
    ("kb_permissions", "ANY", re.compile(r"^/api/v1/kb/(?P<kb_id>[^/]+)/permissions$")),
    ("kb", "ANY", re.compile(r"^/api/v1/kb/(?P<kb_id>[^/]+)$")),
]


@dataclass
class Recorded:
    route: str
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: Any
    at: float
    match: dict[str, str] = field(default_factory=dict)

    @property
    def token_claims(self) -> dict[str, Any]:
        """Unverified claims of the bearer token, i.e. the identity Node forwarded."""
        raw = self.headers.get("authorization", "")
        if not raw.lower().startswith("bearer "):
            return {}
        try:
            return jwt.decode(raw[7:], options={"verify_signature": False})
        except jwt.PyJWTError:
            return {}

    @property
    def user_id(self) -> str | None:
        return self.token_claims.get("userId")


@dataclass
class Reply:
    """A JSON response."""

    body: Any = None
    status: int = 200
    delay: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Gate:
    """Holds a stream until a test opens it. ``reached`` is set when a stream is waiting on it."""

    name: str
    reached: threading.Event = field(default_factory=threading.Event)
    _open: threading.Event = field(default_factory=threading.Event)

    def open(self) -> None:
        self._open.set()

    def wait_reached(self, timeout: float = 15) -> None:
        if not self.reached.wait(timeout):
            raise TimeoutError(f"no stream reached gate {self.name!r} within {timeout}s")


@dataclass
class Frame:
    """One SSE frame. The API speaks AG-UI only: the event name is the AG-UI type and ``data`` carries it too."""

    event: str
    data: Any

    def encode(self) -> bytes:
        body = self.data if isinstance(self.data, str) else json.dumps({"type": self.event, **self.data})
        return f"event: {self.event}\ndata: {body}\n\n".encode()


@dataclass
class Raw:
    """Bytes written as-is."""

    data: bytes


@dataclass
class Pause:
    seconds: float


@dataclass
class Hold:
    gate: Gate


@dataclass
class Drop:
    """Close the connection without finishing the stream."""


@dataclass
class Sse:
    """An SSE response made of steps: Frame, Raw, Pause, Hold, Drop."""

    steps: list[Frame | Raw | Pause | Hold | Drop]
    status: int = 200
    delay: float = 0.0


Scripted = Reply | Sse | Callable[[Recorded], "Reply | Sse"]


def ai_answer(text: str = "Fake answer", **over: Any) -> dict[str, Any]:
    """The JSON the AI backend returns for a finished turn."""
    return {
        "answer": text,
        "citations": [],
        "confidence": "High",
        "reason": "",
        "answerMatchType": "Derived From Blocks",
        "documentIndexes": [],
        "followUpQuestions": [],
        "metadata": {"processingTimeMs": 1, "modelVersion": "fake", "aiTransactionId": "fake-tx"},
        **over,
    }


def text_frame(delta: str, run_id: str = "fake-run") -> Frame:
    return Frame("TEXT_MESSAGE_CONTENT", {"messageId": "fake-msg", "runId": run_id, "delta": delta})


def finished_frame(text: str, run_id: str = "fake-run", **over: Any) -> Frame:
    """The root RUN_FINISHED, whose ``result`` is the finished turn (what the old ``complete`` event carried)."""
    return Frame("RUN_FINISHED", {"threadId": "fake-thread", "runId": run_id, "result": ai_answer(text, **over)})


def stream_answer(text: str = "Fake answer", *, chunks: int = 2, run_id: str = "fake-run", **over: Any) -> Sse:
    """RUN_STARTED, chunked text deltas, then the root RUN_FINISHED carrying the answer."""
    step = max(1, len(text) // chunks)
    pieces = [text[i : i + step] for i in range(0, len(text), step)] or [""]
    steps: list[Frame | Raw | Pause | Hold | Drop] = [Frame("RUN_STARTED", {"threadId": "fake-thread", "runId": run_id})]
    steps += [text_frame(piece, run_id) for piece in pieces]
    steps.append(finished_frame(text, run_id, **over))
    return Sse(steps)


def held_stream(gate: Gate, text: str = "Fake answer", *, run_id: str = "fake-run", **over: Any) -> Sse:
    """Starts, sends the first half of the text, waits on ``gate``, then finishes the answer."""
    first = text[: max(1, len(text) // 2)]
    return Sse(
        [
            Frame("RUN_STARTED", {"threadId": "fake-thread", "runId": run_id}),
            text_frame(first, run_id),
            Hold(gate),
            text_frame(text[len(first) :], run_id),
            finished_frame(text, run_id, **over),
        ]
    )


def ask_user_question(tool_data: dict[str, Any] | None = None, run_id: str = "fake-run") -> Sse:
    """A run that parks on an `ask_user_question` card (the CUSTOM frame Node persists as a tool_call message)."""
    card = tool_data or {"question": "Which environment?", "options": ["staging", "production"]}
    return Sse(
        [
            Frame("RUN_STARTED", {"threadId": "fake-thread", "runId": run_id}),
            Frame("CUSTOM", {"name": "ask_user_question", "value": {"status": "success", "toolData": card}}),
            finished_frame("", run_id, status="waiting_input"),
        ]
    )


def agent_draft_stream(draft: dict[str, Any], text: str = "I drafted it for you to review.", run_id: str = "fake-run") -> Sse:
    """A turn that shows an agent draft card (the `agent_draft` CUSTOM frame Node persists as a `draft_agent` tool_call row), then answers."""
    answer = stream_answer(text, run_id=run_id)
    return Sse([answer.steps[0], Frame("CUSTOM", {"name": "agent_draft", "value": draft}), *answer.steps[1:]])


def run_error(message: str = "boom", code: str = "internal_error") -> Sse:
    return Sse([Frame("RUN_STARTED", {"threadId": "fake-thread", "runId": "fake-run"}), Frame("RUN_ERROR", {"message": message, "code": code})])


@dataclass
class FakeTeam:
    team_id: str
    org_id: str | None
    name: str
    members: dict[str, str]  # userId -> role


class FakeBackend:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.port = 0
        self.requests: list[Recorded] = []
        self._queue: dict[str, deque[Scripted]] = defaultdict(deque)
        self._defaults: dict[str, Scripted] = {}
        self.gates: dict[str, Gate] = {}
        self.teams: dict[str, FakeTeam] = {}
        self._llm_script: list[fake_llm.LlmTurn] = []
        self.llm_default: fake_llm.LlmTurn = fake_llm.llm_turn(fake_llm.DEFAULT_TEXT)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="pcc-e2e-fake-backend", daemon=True)
        self._thread.start()
        if not self._ready.wait(15):
            raise RuntimeError("fake backend did not start")

    def release_gates(self) -> None:
        with self._lock:
            for gate in self.gates.values():
                gate.open()

    def stop(self) -> None:
        if self._loop is None:
            return
        self.release_gates()
        fut = asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop)  # type: ignore[union-attr]
        fut.result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(10)
        self._loop = None

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, "127.0.0.1", 0, shutdown_timeout=2)
        loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self._ready.set()
        loop.run_forever()

    # ---- scripting -------------------------------------------------------------------------

    def reset(self) -> None:
        """Forget requests, queued replies, defaults, gates and teams."""
        self.release_gates()
        with self._lock:
            self.requests.clear()
            self._queue.clear()
            self._defaults.clear()
            self.gates.clear()
            self.teams.clear()
            self._llm_script.clear()
            self.llm_default = fake_llm.llm_turn(fake_llm.DEFAULT_TEXT)

    def script_llm(self, *turns: fake_llm.LlmTurn) -> None:
        """Queue model turns (real-Python mode). A turn answers the first call its ``when`` accepts (no ``when``: any call), in order;
        calls with nothing queued get ``llm_default``."""
        with self._lock:
            self._llm_script.extend(turns)

    def llm_calls(self, since: int = 0) -> list[Recorded]:
        """Chat-completion calls the model server received (after ``mark()`` position ``since``)."""
        return self.since(since, "llm_chat")

    def _next_llm_turn(self, rec: Recorded) -> fake_llm.LlmTurn:
        with self._lock:
            for i, turn in enumerate(self._llm_script):
                if turn.when is None or turn.when(rec.body):
                    return self._llm_script.pop(i)
            return self.llm_default

    def on(self, route: str, *replies: Scripted) -> None:
        """Queue replies for the next requests to ``route`` (each used once, in order)."""
        with self._lock:
            self._queue[route].extend(replies)

    def default(self, route: str, reply: Scripted | None) -> None:
        """Replace the standing reply for ``route``; ``None`` restores the built-in one."""
        with self._lock:
            if reply is None:
                self._defaults.pop(route, None)
            else:
                self._defaults[route] = reply

    @contextmanager
    def failing(self, route: str, status: int = 503, body: Any = None, delay: float = 0.0) -> Iterator[None]:
        """Every request to ``route`` fails inside the block."""
        self.default(route, Reply(body if body is not None else {"error": "fake backend failure"}, status=status, delay=delay))
        try:
            yield
        finally:
            self.default(route, None)

    def gate(self, name: str) -> Gate:
        with self._lock:
            return self.gates.setdefault(name, Gate(name))

    def requests_for(self, *routes: str) -> list[Recorded]:
        with self._lock:
            return [r for r in self.requests if r.route in routes]

    def mark(self) -> int:
        """A position in the request log; pass to ``since``."""
        with self._lock:
            return len(self.requests)

    def since(self, mark: int, *routes: str) -> list[Recorded]:
        with self._lock:
            return [r for r in self.requests[mark:] if not routes or r.route in routes]

    def wait_for_request(self, route: str, count: int = 1, timeout: float = 15) -> list[Recorded]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.requests_for(route)
            if len(found) >= count:
                return found
            time.sleep(0.05)
        raise TimeoutError(f"expected {count} request(s) to {route!r}, saw {len(self.requests_for(route))}")

    def add_team(self, org_id: str | None, name: str, members: dict[str, str] | None = None, team_id: str | None = None) -> FakeTeam:
        team = FakeTeam(team_id or str(uuid.uuid4()), org_id, name, dict(members or {}))
        with self._lock:
            self.teams[team.team_id] = team
        return team

    # ---- serving ---------------------------------------------------------------------------

    def _classify(self, method: str, path: str) -> tuple[str, dict[str, str]]:
        for name, m, rx in ROUTES:
            if m not in (method, "ANY"):
                continue
            hit = rx.match(path)
            if hit:
                return name, hit.groupdict()
        return "unmatched", {}

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        route, match = self._classify(request.method, request.path)
        raw = await request.read()
        body: Any = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode(errors="replace")
        rec = Recorded(
            route=route,
            method=request.method,
            path=request.path,
            query=dict(request.query),
            headers={k.lower(): v for k, v in request.headers.items()},
            body=body,
            at=time.time(),
            match=match,
        )
        with self._lock:
            self.requests.append(rec)
            queued = self._queue[route].popleft() if self._queue[route] else None
            scripted = queued if queued is not None else self._defaults.get(route)
        if scripted is None and route == "llm_chat":
            turn = self._next_llm_turn(rec)
            if turn.delay:
                await asyncio.sleep(turn.delay)
            payload, is_sse = turn.render(rec.body if isinstance(rec.body, dict) else {})
            content_type = "text/event-stream" if is_sse else "application/json"
            return web.Response(body=payload, content_type=content_type)
        if scripted is None:
            scripted = self._builtin(rec)
        elif callable(scripted):
            scripted = scripted(rec)
        return await self._send(request, scripted)

    async def _send(self, request: web.Request, reply: Reply | Sse) -> web.StreamResponse:
        if reply.delay:
            await asyncio.sleep(reply.delay)
        if isinstance(reply, Reply):
            return web.json_response(reply.body, status=reply.status, headers=reply.headers)
        resp = web.StreamResponse(
            status=reply.status,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        await resp.prepare(request)
        try:
            for step in reply.steps:
                if isinstance(step, Frame):
                    await resp.write(step.encode())
                elif isinstance(step, Raw):
                    await resp.write(step.data)
                elif isinstance(step, Pause):
                    await asyncio.sleep(step.seconds)
                elif isinstance(step, Hold):
                    step.gate.reached.set()
                    while not step.gate._open.is_set():  # noqa: SLF001
                        await asyncio.sleep(0.02)
                elif isinstance(step, Drop):
                    request.transport.abort()  # type: ignore[union-attr]
                    return resp
            await resp.write_eof()
        except (ConnectionResetError, ClientConnectionResetError):
            pass  # Node went away (it aborts the upstream request when its own client disconnects)
        return resp

    # ---- built-in behaviour ----------------------------------------------------------------

    def _builtin(self, rec: Recorded) -> Reply | Sse:
        name = rec.route
        if name == "health":
            return Reply({"status": "healthy"})
        if name == "llm_embeddings":
            return Reply(fake_llm.embeddings_response(rec.body if isinstance(rec.body, dict) else {}))
        if name == "llm_models":
            return Reply(fake_llm.models_response())
        if name == "model_health_check":
            return Reply({"status": "success", "message": "healthy"})
        if name == "connectors_list":
            return Reply({"success": True, "connectors": [], "pagination": {"page": 1, "limit": 100, "total": 0, "totalPages": 0}})
        if name == "demo_data_status":
            return Reply({"success": True, "status": "none", "demoDataEnabled": False})
        if name == "my_toolsets":
            # The composer's tool picker loads this on every chat page; unanswered, Node's 404 surfaced as a "Not Found" toast.
            empty = {"page": 1, "limit": 200, "total": 0, "totalPages": 0, "hasNext": False, "hasPrev": False}
            return Reply({"status": "success", "toolsets": [], "pagination": empty, "filterCounts": {"all": 0, "authenticated": 0, "notAuthenticated": 0}})
        if name in ("chat", "agent_chat"):
            return Reply(ai_answer())
        if name in ("chat_stream", "agent_chat_stream"):
            return stream_answer()
        if name in ("chat_cancel", "chat_cancel_participant"):
            return Reply({"status": "success", "message": "cancelled"})
        if name == "attachments_validate":
            ids = (rec.body or {}).get("recordIds", []) if isinstance(rec.body, dict) else []
            return Reply({"recordIds": ids})
        if name == "agent_create":
            return Reply({"status": "success", "agent": {"_key": "agent-new", "name": (rec.body or {}).get("name", "agent")}})
        if name == "agent_create_from_chat":
            body = rec.body if isinstance(rec.body, dict) else {}
            return Reply({"status": "success", "agent": {"_key": "agent-from-chat", "name": body.get("name", "agent"), "handle": body.get("handle", "agent-from-chat")}})
        if name == "agent_handle_availability":
            return Reply({"available": True})
        if name == "agent_list":
            return Reply({"status": "success", "agents": [], "pagination": {"currentPage": 1, "limit": 20, "totalItems": 0, "totalPages": 0}})
        if name == "agent_item":
            return Reply({"status": "success", "agent": {"_key": rec.match["agent_key"], "name": "Agent", "can_edit": True}})
        if name == "agent_readiness":
            return Reply({"canSend": True, "missingToolsets": [], "unauthenticatedToolsets": []})
        if name == "agent_service_account":
            return Reply({"isServiceAccount": False})
        if name == "team_ids":
            uid = rec.user_id
            ids = [t.team_id for t in self.teams.values() if uid in t.members and t.org_id == rec.token_claims.get("orgId")]
            return Reply({"teamIds": ids})
        if name in ("team_get", "team_users", "team_update", "team_delete", "team_create", "user_teams"):
            return self._teams_api(rec)
        if name == "kb_create":
            return Reply({"id": f"kb-{uuid.uuid4().hex[:8]}"})
        if name in ("kb", "kb_permissions"):
            return Reply({"status": "success"})
        return Reply({"error": f"fake backend has no route for {rec.method} {rec.path}"}, status=404)

    @staticmethod
    def _team_json(team: FakeTeam) -> dict[str, Any]:
        return {
            "id": team.team_id,
            "_key": team.team_id,
            "name": team.name,
            "orgId": team.org_id,
            "users": [{"userId": u, "role": r} for u, r in team.members.items()],
        }

    def _teams_api(self, rec: Recorded) -> Reply:
        claims = rec.token_claims
        org_id, caller = claims.get("orgId"), claims.get("userId")
        body = rec.body if isinstance(rec.body, dict) else {}
        team_id = rec.match.get("team_id")
        team = self.teams.get(team_id) if team_id else None
        if rec.route == "team_create":
            members = {ur["userId"]: ur["role"] for ur in body.get("userRoles", [])}
            members.setdefault(caller, "OWNER")
            created = self.add_team(org_id, body.get("name", "team"), members)
            return Reply({"status": "success", "data": self._team_json(created)}, status=201)
        if rec.route == "user_teams":
            mine = [self._team_json(t) for t in self.teams.values() if caller in t.members and t.org_id == org_id]
            return Reply({"status": "success", "teams": mine, "pagination": {"page": 1, "limit": 100, "total": len(mine)}})
        if team is None or team.org_id != org_id:
            return Reply({"status": "error", "message": "Team not found"}, status=404)
        if rec.route == "team_delete":
            with self._lock:
                self.teams.pop(team.team_id, None)
            return Reply({"status": "success"})
        if rec.route == "team_update":
            for ur in body.get("addUserRoles", []) + body.get("updateUserRoles", []):
                team.members[ur["userId"]] = ur["role"]
            for uid in body.get("removeUserIds", []):
                team.members.pop(uid, None)
            if "name" in body:
                team.name = body["name"]
        return Reply({"status": "success", "team": self._team_json(team)})
