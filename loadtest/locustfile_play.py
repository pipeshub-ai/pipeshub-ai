"""Load test for PipesHub chat and agent streaming.

Measures where the query service stops keeping up, and — more importantly —
whether its event loop is being blocked. The primary signal is HEALTH:probe
latency: /health is a trivial async endpoint that does no I/O, so any time it
takes more than a few milliseconds to answer, the loop was busy or blocked.

Run against a resource-pinned Docker stack (see docker-compose.loadtest.yml).
Numbers from an unpinned stack are not reproducible.

Usage:
    # Many identities (required to A/B anything cached per user — see seed_users.py)
    export PIPESHUB_USERS='a@example.com:Pass1!,b@example.com:Pass2!'
    # or a single one
    export PIPESHUB_TOKEN='<bearer token from browser devtools>'
    # PIPESHUB_TOKEN wins; TOKEN is the documented .env fallback (same as perftest.sh)
    locust -f locustfile.py --headless -u 10 -r 2 -t 10m -H http://localhost:3000

    # ramp to find the knee
    PIPESHUB_SHAPE=stepramp locust -f locustfile.py --headless \
        -t 25m -H http://localhost:3000

Scenario classes (name them on the CLI to pin the mix):
    InternalSearchUser   plain RAG chat
    AgentUser            agent with tools  <- the one that shows loop blocking
    HealthProbeUser      always on, independent of -u
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path
import time

from locust import HttpUser, LoadTestShape, constant, events, task

sys.path.insert(0, str(Path(__file__).parent))
from pipeshub_auth import AuthError, credentials_from_env, resolve_tokens  # noqa: E402

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

# Prefer PIPESHUB_TOKEN; fall back to TOKEN so loadtest/.env matches perftest.sh.
TOKEN = os.environ.get("PIPESHUB_TOKEN") or os.environ.get("TOKEN") or ""
AGENT_KEY = os.environ.get("PIPESHUB_AGENT_KEY", "")

# Tokens the simulated users draw from, resolved once at test_start. One shared
# token measures a per-user cache as if every request came from the same person,
# so multi-user runs are the only honest way to A/B one.
TOKENS: list[str] = []
_token_cursor = itertools.count()

# The query service, exposed directly by the loadtest compose overlay. Probing
# through the Node gateway would fold Node's latency into the measurement.
HEALTH_URL = os.environ.get("PIPESHUB_HEALTH_URL", "http://localhost:8000/health")
HEALTH_INTERVAL = float(os.environ.get("PIPESHUB_HEALTH_INTERVAL", "1.0"))
HEALTH_PROBES = int(os.environ.get("PIPESHUB_HEALTH_PROBES", "1"))
# Anything above this means the loop was blocked. 100ms is the widely used
# threshold for "something is wrong"; a healthy idle loop answers in <10ms.
HEALTH_SLA_MS = float(os.environ.get("PIPESHUB_HEALTH_SLA_MS", "100"))

STREAM_TIMEOUT = float(os.environ.get("PIPESHUB_STREAM_TIMEOUT", "300"))
THINK_TIME = float(os.environ.get("PIPESHUB_THINK_TIME", "3"))

def _load_questions() -> list[str]:
    """Shared with `perftest.sh` so both harnesses ask the same thing; the
    inline list is the fallback when this file is run from elsewhere."""
    path = Path(os.environ.get("PIPESHUB_QUERY_FILE", Path(__file__).with_name("queries.txt")))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ["What are the key features described in the documents?"]
    found = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    return found or ["What are the key features described in the documents?"]


QUESTIONS = _load_questions()

# Questions phrased to push an agent toward using its tools rather than
# answering from retrieved context alone.
AGENT_QUESTIONS = [
    "Search my Drive for the most recently modified document and summarise it.",
    "Find files shared with me in the last week and list them.",
    "Look up recent messages and tell me what was discussed.",
    "Check my documents for anything about deployment and summarise it.",
]


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


@events.test_start.add_listener
def _resolve_identities(environment, **_kwargs) -> None:
    """Turn credentials into tokens before the clock starts.

    Logging in here rather than per user keeps the auth round trips out of the
    measurement window, and fails the run up front if a password is wrong —
    five bad attempts lock the account for 24h.
    """
    global TOKENS

    try:
        credentials = credentials_from_env()
    except AuthError as e:
        raise SystemExit(str(e)) from e

    if credentials:
        host = environment.host or os.environ.get("PIPESHUB_HOST", "http://localhost:3000")
        try:
            TOKENS = resolve_tokens(host, credentials)
        except AuthError as e:
            raise SystemExit(f"Could not log in load-test users: {e}") from e
        print(f"[loadtest] resolved {len(TOKENS)} user tokens via password login")
        return

    if TOKEN:
        TOKENS = [TOKEN]
        which = "PIPESHUB_TOKEN" if os.environ.get("PIPESHUB_TOKEN") else "TOKEN"
        print(f"[loadtest] using the single {which} identity")
        return

    raise SystemExit(
        "No credentials. Set PIPESHUB_USERS='email:password,...' (see seed_users.py), "
        "or PIPESHUB_EMAILS + PIPESHUB_PASSWORD, or PIPESHUB_TOKEN / TOKEN for a single user."
    )


# --------------------------------------------------------------------------
# SSE handling
# --------------------------------------------------------------------------


def consume_sse(response, on_event) -> None:
    """Feed (event_type, data) to on_event for each SSE frame.

    PipesHub emits `event: <type>\\ndata: <json>\\n\\n` (utils/streaming.py).
    Blank-line-delimited frames, so track the current event type across lines.
    """
    event_type = "message"
    for raw in response.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.strip()
        if not line:
            event_type = "message"
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"raw": payload}
            on_event(event_type, data)


class StreamingChatUser(HttpUser):
    """Base: POST a streaming chat turn and time each milestone.

    Milestones are reported as separate named requests so Locust gives you
    percentiles per milestone rather than only for the whole turn.
    """

    abstract = True
    wait_time = constant(THINK_TIME)

    prefix: str = "chat"
    questions: list[str] = QUESTIONS
    token: str = ""
    # `/conversations/stream` rejects a request without an explicit chatMode
    # (es_validators.ts: UNIVERSAL_STREAM_CHAT_MODES). It used to default, so
    # omitting it silently 400s every request rather than failing loudly.
    chat_mode: str = "internal_search"

    def on_start(self) -> None:
        # Round-robin so identities are spread evenly however Locust ramps.
        self.token = TOKENS[next(_token_cursor) % len(TOKENS)] if TOKENS else TOKEN

    def endpoint(self) -> str:
        raise NotImplementedError

    def _fire(self, name: str, start: float, exc: Exception | None = None) -> None:
        self.environment.events.request.fire(
            request_type="SSE",
            name=f"{self.prefix}:{name}",
            response_time=(time.monotonic() - start) * 1000,
            response_length=0,
            exception=exc,
            context={},
        )

    @task
    def turn(self) -> None:
        question = self.questions[int(time.monotonic() * 1000) % len(self.questions)]
        body = {"query": question, "chatMode": self.chat_mode}
        _mk = os.environ.get("PIPESHUB_MODEL_KEY")
        if _mk:
            body["modelKey"] = _mk
        # Retrieval breadth. Unset, the API applies `limit: int | None = 50`
        # (chatbot.py:53), which produced ~33k tokens of context re-sent on
        # every LLM call of the turn. Set this to A/B that directly.
        _lim = os.environ.get("PIPESHUB_LIMIT")
        if _lim:
            body["limit"] = int(_lim)
        # Reasoning depth. Unset, aimodels applies DEFAULT_REASONING_EFFORT
        # ("high"), which dominates turn latency on a reasoning model -- set
        # this to A/B it. Values: none|low|medium|high|max ("none" is floored
        # to "low" by _reasoning_effort_kwargs).
        _eff = os.environ.get("PIPESHUB_REASONING_EFFORT")
        if _eff:
            body["reasoningEffort"] = _eff

        start = time.monotonic()
        seen_first_packet = False
        seen_first_token = False
        tokens = 0

        try:
            with self.client.post(
                self.endpoint(),
                json=body,
                headers=_auth_headers(self.token),
                stream=True,
                timeout=STREAM_TIMEOUT,
                catch_response=True,
                name=f"{self.prefix}:POST",
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"HTTP {resp.status_code}: {resp.text[:300]}")
                    self._fire("total_turn", start, Exception(f"HTTP {resp.status_code}"))
                    return

                # Event names verified against a live stream 2026-07-28:
                #   connected | status | answer_chunk | metadata | tool_result | complete
                st = {"first_packet": False, "first_token": False, "first_tool": False}

                def on_event(event_type: str, data: dict) -> None:
                    nonlocal tokens
                    if not st["first_packet"]:
                        st["first_packet"] = True
                        self._fire("first_packet", start)

                    # Play/AG-UI protocol: event type is inside the JSON payload,
                    # emitted as bare `data: {...}` lines with no `event:` header.
                    etype = data.get("type") if isinstance(data, dict) else None
                    if etype in ("TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_START"):
                        tokens += 1
                        if not st["first_token"]:
                            st["first_token"] = True
                            self._fire("first_answer_token", start)
                    elif etype == "TOOL_CALL_RESULT" and not st["first_tool"]:
                        st["first_tool"] = True
                        self._fire("first_tool_result", start)
                    elif etype == "RUN_ERROR":
                        raise RuntimeError(str(data)[:300])

                    if event_type == "answer_chunk":
                        tokens += 1
                        if not st["first_token"]:
                            st["first_token"] = True
                            self._fire("first_answer_token", start)

                    # Agent runs only: proves the toolset actually fired, and
                    # times how long the (currently loop-blocking) call took.
                    elif event_type == "tool_result" and not st["first_tool"]:
                        st["first_tool"] = True
                        self._fire("first_tool_result", start)

                    elif event_type == "error" or (
                        isinstance(data, dict) and data.get("error")
                    ):
                        raise RuntimeError(str(data)[:300])

                consume_sse(resp, on_event)
                seen_first_packet = st["first_packet"]
                seen_first_token = st["first_token"]

                if not seen_first_packet:
                    resp.failure("stream produced no events")
                else:
                    resp.success()

        except Exception as exc:  # noqa: BLE001 - report, don't crash the user
            self._fire("total_turn", start, exc)
            return

        self._fire("total_turn", start, None if seen_first_token else
                   Exception("no answer content in stream"))


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


class InternalSearchUser(StreamingChatUser):
    """Plain RAG chat — retrieval then LLM, no tools."""

    prefix = "chat"
    weight = 3

    def endpoint(self) -> str:
        return "/api/v1/conversations/stream"


class ShortAnswerUser(StreamingChatUser):
    """Same retrieval path as InternalSearchUser, but forces a very short
    answer. Diagnostic for F2: if loop blocking drops sharply versus
    InternalSearchUser at equal concurrency, the blocking scales with answer
    length (the O(N^2) `accumulated` SSE payload). If it doesn't, the cause is
    retrieval-side and answer length is irrelevant.
    """

    prefix = "short"
    weight = 1  # opt-in by naming it on the CLI (weight 0 would never spawn)
    questions = [
        q + " Answer in one short sentence, under 15 words. Do not elaborate."
        for q in QUESTIONS
    ]

    def endpoint(self) -> str:
        return "/api/v1/conversations/stream"


class AgentUser(StreamingChatUser):
    """Agent with tools. This is the scenario that surfaces loop blocking:
    each Google/Slack tool call runs on the event loop today."""

    prefix = "agent"
    weight = 1
    questions = AGENT_QUESTIONS
    # The agent route accepts only 'quick' (es_validators.ts: AGENT_CHAT_MODES).
    chat_mode = "quick"

    def endpoint(self) -> str:
        if not AGENT_KEY:
            raise SystemExit("PIPESHUB_AGENT_KEY is not set — AgentUser cannot run.")
        return f"/api/v1/agents/{AGENT_KEY}/conversations/stream"


class HealthProbeUser(HttpUser):
    """Hits /health on a fixed cadence, independent of -u.

    This is the experiment's primary signal. /health does no I/O, so its
    latency IS event loop lag. When HEALTH:probe starts failing, the loop is
    blocked — which is exactly the failure we're hunting.
    """

    fixed_count = HEALTH_PROBES
    wait_time = constant(HEALTH_INTERVAL)

    @task
    def probe(self) -> None:
        start = time.monotonic()
        try:
            r = self.client.get(
                HEALTH_URL, name="HEALTH:probe", timeout=30, catch_response=True
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            with r:
                if r.status_code != 200:
                    r.failure(f"HTTP {r.status_code}")
                elif elapsed_ms > HEALTH_SLA_MS:
                    r.failure(f"event loop lag: {elapsed_ms:.0f}ms > {HEALTH_SLA_MS:.0f}ms")
                else:
                    r.success()
        except Exception as exc:  # noqa: BLE001
            self.environment.events.request.fire(
                request_type="GET",
                name="HEALTH:probe",
                response_time=(time.monotonic() - start) * 1000,
                response_length=0,
                exception=exc,
                context={},
            )


# --------------------------------------------------------------------------
# Ramp shape (opt-in via PIPESHUB_SHAPE=stepramp)
# --------------------------------------------------------------------------


# Locust auto-activates ANY LoadTestShape subclass present in the file and lets
# it override -u/-r. A disabled shape whose tick() returns None reads as "stop
# the test", so the class must not exist at all unless it's wanted.
if os.environ.get("PIPESHUB_SHAPE", "") == "stepramp":

    class StepRampShape(LoadTestShape):
        """Hold at plateaus so the knee is visible in the timeline."""

        def __init__(self) -> None:
            super().__init__()
            raw = os.environ.get("PIPESHUB_RAMP_STAGES", "5,10,25,50,100")
            self.stages = [int(p) for p in raw.split(",") if p.strip()] or [5, 10, 25]
            self.dwell = max(30, int(os.environ.get("PIPESHUB_RAMP_DWELL", "180")))
            self.spawn = max(0.5, float(os.environ.get("PIPESHUB_RAMP_SPAWN", "2")))

        def tick(self):
            run_time = self.get_run_time()
            for i, users in enumerate(self.stages, start=1):
                if run_time < i * self.dwell:
                    return users, self.spawn
            return None
