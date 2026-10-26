"""The push routes called on Python directly and admission."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Never
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middlewares.auth import require_scopes
from app.connectors.sources.localKB.api import kb_router as R
from app.connectors.sources.localKB.push import admission as A
from app.connectors.sources.localKB.push import api as push_api
from app.connectors.sources.localKB.push import gate
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.service import KbPushService, PushResult
from app.core import org_context
from app.modules.record_metadata.schemas import MetadataSchemaRegistry

from .bodies import envelope, raw

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from starlette.responses import Response

ROLES = {"owner": "OWNER", "writer": "WRITER", "reader": "READER", "commenter": "COMMENTER"}


class FakeConfig:
    def __init__(self) -> None:
        self.data = {}
        self.logger = MagicMock()

    async def get_config(self, key, default=None, use_cache=True, raise_on_error=False) -> object:
        return self.data.get(key, default)

    async def create_config_if_absent(self, key, value) -> bool:
        return self.data.setdefault(key, value) is value

    async def set_config(self, key, value) -> bool:
        self.data[key] = value
        return True


class FakeKb:
    async def _resolve_user_and_kb_access(self, kb_id, user_id, required_roles=None, permission_denied_reason=None) -> tuple:
        role = ROLES.get(user_id)
        if role is None:
            return None, None, {"success": False, "code": 404, "reason": "Knowledge base not found"}
        if required_roles and role not in required_roles:
            return None, None, {"success": False, "code": 403, "reason": "no"}
        return f"key-{user_id}", role, None


class Harness:
    def __init__(self) -> None:
        self.worker = MagicMock()
        self.worker.validate = AsyncMock()
        self.spool = MagicMock()
        self.pushed = []
        self.admission = A.Admission()
        self.schemas = MetadataSchemaRegistry(FakeConfig())
        self.user = "writer"

    def service(self) -> KbPushService:
        svc = KbPushService(
            graph_provider=MagicMock(), kb_service=FakeKb(), processor_for_kb=AsyncMock(), schema_registry=self.schemas,
            storage=MagicMock(), worker=self.worker, spool=self.spool, publish_event=AsyncMock(),
        )

        async def push(**kw) -> PushResult:
            self.pushed.append(kw)
            return PushResult("created", 201, "rid", kw["kb_id"], "k", 1, True, "abc", None, "newRecord", "QUEUED")

        svc.push = push
        return svc


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[None]:
    """The auth dependency binds the request's org; tests that call a route function directly bind it here."""
    token = org_context.push(org_context.OrgContext("org1", "writer", "user", "jwt", "-"))
    yield
    org_context.pop(token)


@pytest.fixture
def h(monkeypatch) -> Harness:
    harness = Harness()
    monkeypatch.setattr(R, "_push_service", lambda request, kb, kafka: harness.service())
    monkeypatch.setattr(push_api, "runtime_for", lambda app: MagicMock(admission=harness.admission))
    harness.flag = True

    async def flag(_config) -> bool:
        return harness.flag

    monkeypatch.setattr(gate, "is_kb_push_enabled", flag)
    return harness


@pytest.fixture
def client(h) -> TestClient:
    app = FastAPI()
    app.include_router(R.kb_router)
    app.include_router(R.kb_push_router)
    app.add_middleware(gate.KbPushFlagMiddleware)
    app.container = MagicMock()
    app.state.graph_provider = AsyncMock()
    app.dependency_overrides[R.get_kb_service] = lambda: MagicMock()
    app.dependency_overrides[R.get_kafka_service] = lambda: MagicMock()
    app.dependency_overrides[require_scopes] = lambda *a, **k: (lambda: None)

    @app.middleware("http")
    async def inject_user(request, call_next) -> Response:
        request.state.user = {"userId": h.user, "orgId": "org1"}
        org_context.set_request_context(org_context.OrgContext("org1", h.user, "user", "jwt", "-"))
        return await call_next(request)

    return TestClient(app)


JSON = {"content-type": "application/json"}


def error(resp) -> dict:
    return resp.json()["error"]


# ---- the POST ---------------------------------------------------------------------------------------

def test_push_success_is_201_with_the_response_shape(client, h) -> None:
    r = client.post("/api/v1/kb/kb1/records?folderId=f1", content=raw(envelope()), headers=JSON)
    assert r.status_code == 201
    assert r.json()["status"] == "created" and r.json()["event"] == "newRecord"
    assert h.pushed[0]["folder_id"] == "f1" and h.pushed[0]["org_id"] == "org1" and h.pushed[0]["user_id"] == "writer"


@pytest.mark.parametrize("user,status,reason", [("stranger", 404, "KB_NOT_FOUND"), ("reader", 403, "KB_ROLE_INSUFFICIENT"), ("commenter", 403, "KB_ROLE_INSUFFICIENT")])
def test_roles(client, h, user, status, reason) -> None:
    h.user = user
    spy = AsyncMock(wraps=h.admission.read_body)
    h.admission.read_body = spy
    r = client.post("/api/v1/kb/kb1/records", content=raw(envelope()), headers=JSON)
    assert (r.status_code, error(r)["reason"]) == (status, reason)
    assert spy.await_count == 0 and not h.pushed  # the body was never read


def test_other_orgs_user_never_reaches_the_body(client, h) -> None:
    h.user = "someone-in-org-b"
    spy = AsyncMock(wraps=h.admission.read_body)
    h.admission.read_body = spy
    r = client.post("/api/v1/kb/kb-of-org-a/records", content=raw(envelope()), headers=JSON)
    assert r.status_code == 404 and spy.await_count == 0


def test_not_json_is_415(client) -> None:
    r = client.post("/api/v1/kb/kb1/records", content=b"{}", headers={"content-type": "text/plain"})
    assert (r.status_code, error(r)["reason"]) == (415, "UNSUPPORTED_MEDIA_TYPE")


def test_body_over_the_cap_by_content_length_is_413_without_a_parse(client, h) -> None:
    r = client.post("/api/v1/kb/kb1/records", content=b"x" * ((16 << 20) + 1), headers=JSON)
    e = error(r)
    assert (r.status_code, e["reason"], e["limit"], e["limitBytes"]) == (413, "PAYLOAD_TOO_LARGE", "body", 16 << 20)
    assert not h.pushed and h.worker.validate.await_count == 0


def test_chunked_body_over_the_cap_is_413(client, h) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(17):
            yield b"x" * (1 << 20)

    r = client.post("/api/v1/kb/kb1/records", content=chunks(), headers=JSON)
    assert r.status_code == 413 and not h.pushed


def test_a_client_header_claiming_a_larger_limit_is_ignored(client, h) -> None:
    r = client.post("/api/v1/kb/kb1/records", content=b"x" * ((16 << 20) + 1), headers={**JSON, "x-max-body-bytes": "999999999", "x-limit": "1g"})
    assert r.status_code == 413


def test_body_just_under_the_cap_is_read(client, h) -> None:
    r = client.post("/api/v1/kb/kb1/records", content=b"x" * ((16 << 20) - 1), headers=JSON)
    assert r.status_code == 201 and len(h.pushed) == 1


def test_a_second_push_by_the_same_user_is_429(client, h) -> None:
    h.admission._in_flight.add("key-writer")
    r = client.post("/api/v1/kb/kb1/records", content=raw(envelope()), headers=JSON)
    assert (r.status_code, error(r)["reason"], r.headers["retry-after"]) == (429, "PUSH_BUSY", "1")


def test_refusal_from_the_push_becomes_the_error_body(client, h) -> None:
    async def refuse(**kw) -> Never:
        raise PushRefusal(409, "STALE_VERSION", "newer", extra={"currentVersion": 7})

    svc = h.service()
    svc.push = refuse
    h.service = lambda: svc
    r = client.post("/api/v1/kb/kb1/records", content=raw(envelope()), headers=JSON)
    assert r.status_code == 409 and error(r) == {"code": "HTTP_CONFLICT", "reason": "STALE_VERSION", "message": "newer", "currentVersion": 7}


def test_property_refusal_names_the_property(client, h) -> None:
    async def refuse(**kw) -> Never:
        raise PushRefusal(422, "PROPERTY_RESERVED", "reserved", "orgId")

    svc = h.service()
    svc.push = refuse
    h.service = lambda: svc
    e = error(client.post("/api/v1/kb/kb1/records", content=raw(envelope()), headers=JSON))
    assert (e["reason"], e["property"], e["location"]) == ("PROPERTY_RESERVED", "orgId", "orgId")


async def test_a_disconnect_after_the_last_body_byte_does_not_cancel_the_push(h) -> None:
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_push(**kw) -> PushResult:
        started.set()
        await release.wait()
        finished.set()
        return PushResult("created", 201, "rid", "kb1", "k", 1, True, "abc", None, "newRecord", "QUEUED")

    svc = h.service()
    svc.push = slow_push
    h.service = lambda: svc

    async def stream() -> AsyncIterator[bytes]:
        yield raw(envelope())

    request = MagicMock()
    request.state.user = {"userId": "writer", "orgId": "org1"}
    request.headers = JSON
    request.stream = stream
    task = asyncio.create_task(R.push_record("kb1", request, None, MagicMock(), MagicMock()))
    await started.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(finished.wait(), 2)


# ---- admission -----------------------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def slow_stream(clock, chunk_bytes, seconds_per_chunk, chunks) -> AsyncIterator[bytes]:
    for _ in range(chunks):
        clock.t += seconds_per_chunk
        yield b"x" * chunk_bytes


async def test_a_one_byte_per_second_sender_gets_408() -> None:
    clock = Clock()
    adm = A.Admission(clock=clock)
    with pytest.raises(PushRefusal) as e:
        await adm.read_body({"content-type": "application/json", "content-length": "1000"}, slow_stream(clock, 1, 1.0, 10))
    assert (e.value.status, e.value.reason) == (408, "BODY_TIMEOUT")


async def test_a_sender_at_the_minimum_rate_is_accepted_for_the_whole_deadline() -> None:
    clock = Clock()
    adm = A.Admission(clock=clock)
    size = 2 << 20
    chunk = 66 * 1024
    chunks = size // chunk + 1
    body = await adm.read_body({"content-type": "application/json", "content-length": str(chunks * chunk)}, slow_stream(clock, chunk, 1.0, chunks))
    assert len(body) == chunks * chunk


def test_deadline_grows_with_the_body_and_is_capped() -> None:
    assert A.read_deadline_s(0) == 5.0
    assert A.read_deadline_s(16 << 20) == pytest.approx(5 + 256)
    assert A.read_deadline_s(None) == A.MAX_READ_S


async def test_a_full_set_of_slots_gives_503_after_the_wait() -> None:
    adm = A.Admission(read_slots=1, slot_wait_s=0.05)
    gate = asyncio.Event()

    async def held() -> AsyncIterator[bytes]:
        await gate.wait()
        yield b"{}"

    first = asyncio.create_task(adm.read_body({"content-type": "application/json"}, held()))
    await asyncio.sleep(0.01)

    async def empty() -> AsyncIterator[bytes]:
        yield b""

    with pytest.raises(PushRefusal) as e:
        await adm.read_body({"content-type": "application/json"}, empty())
    assert (e.value.status, e.value.reason) == (503, "PUSH_BUSY")
    gate.set()
    await first


# ---- schema routes ------------------------------------------------------------------------------

TEAM = {"name": "team", "type": "STRING", "displayLabel": "Team", "filterable": True, "facetable": True}
TIER = {"name": "tier", "type": "ENUM", "displayLabel": "Tier", "enumValues": ["P0", "P1"], "filterable": True}


def put(client, props) -> Response:
    return client.put("/api/v1/kb/kb1/schema", content=json.dumps({"properties": props}), headers=JSON)


def test_schema_put_is_versioned_and_idempotent(client, h) -> None:
    h.user = "owner"
    a = put(client, [TEAM])
    assert a.status_code == 200 and (a.json()["schemaId"], a.json()["version"], a.json()["created"]) == ("org1:kb1:1", 1, True)
    again = put(client, [TEAM])
    assert (again.json()["schemaId"], again.json()["created"]) == ("org1:kb1:1", False)
    b = put(client, [TEAM, TIER])
    assert (b.json()["schemaId"], b.json()["created"]) == ("org1:kb1:2", True)
    reordered = put(client, [TIER, TEAM])
    assert (reordered.json()["schemaId"], reordered.json()["created"]) == ("org1:kb1:2", False)
    assert b.json()["properties"][0]["facetable"] is True and b.json()["properties"][1]["enumValues"] == ["P0", "P1"]


def test_schema_put_by_a_writer_is_403(client, h) -> None:
    h.user = "writer"
    r = put(client, [TEAM])
    assert (r.status_code, error(r)["reason"]) == (403, "KB_ROLE_INSUFFICIENT")
    assert asyncio.run(h.schemas.get("org1", "kb1")) is None


@pytest.mark.parametrize("props,status,reason,loc", [
    ([{**TEAM, "name": "orgId"}], 422, "PROPERTY_DEFINITION_INVALID", "properties[0]"),
    ([TEAM, {**TIER, "enumValues": []}], 422, "PROPERTY_DEFINITION_INVALID", "properties[1]"),
    ([{**TEAM, "type": "NOPE"}], 422, "PROPERTY_DEFINITION_INVALID", "properties[0]"),
    ([{**TEAM, "unknown": 1}], 400, "ENVELOPE_INVALID", None),
    ([{**TEAM, "filterable": "yes"}], 400, "ENVELOPE_INVALID", None),
])
def test_schema_put_invalid(client, h, props, status, reason, loc) -> None:
    h.user = "owner"
    r = put(client, props)
    e = error(r)
    assert (r.status_code, e["reason"]) == (status, reason)
    if loc:
        assert e["location"] == loc and e["property"]


def test_schema_get_latest_and_versions_for_a_reader(client, h) -> None:
    h.user = "owner"
    put(client, [TEAM])
    put(client, [TEAM, TIER])
    h.user = "reader"
    latest = client.get("/api/v1/kb/kb1/schema")
    assert latest.status_code == 200 and latest.json()["version"] == 2
    one = client.get("/api/v1/kb/kb1/schema?version=1")
    assert one.json()["schemaId"] == "org1:kb1:1" and len(one.json()["properties"]) == 1
    missing = client.get("/api/v1/kb/kb1/schema?version=9")
    assert (missing.status_code, error(missing)["reason"]) == (404, "SCHEMA_NOT_FOUND")


def test_schema_get_with_no_schema_and_without_a_role(client, h) -> None:
    h.user = "reader"
    r = client.get("/api/v1/kb/kb1/schema")
    assert (r.status_code, error(r)["reason"]) == (404, "SCHEMA_NOT_FOUND")
    h.user = "stranger"
    r = client.get("/api/v1/kb/kb1/schema")
    assert (r.status_code, error(r)["reason"]) == (404, "KB_NOT_FOUND")


# ---- ENABLE_KB_PUSH: off by default, the routes answer as if absent ----------------------------------------

def test_with_the_flag_on_the_routes_work(client, h) -> None:
    h.flag = True
    assert client.post("/api/v1/kb/kb1/records", content=raw(envelope()), headers=JSON).status_code == 201


async def test_the_flag_defaults_to_off() -> None:
    from app.services.featureflag.config.config import CONFIG
    from app.services.featureflag.platform_settings import is_kb_push_enabled

    assert CONFIG.ENABLE_KB_PUSH == "ENABLE_KB_PUSH"
    assert await is_kb_push_enabled(None) is False
    config = MagicMock()
    config.get_config = AsyncMock(return_value=None)
    assert await is_kb_push_enabled(config) is False


# ---- the deadline is a timer, and one push per caller covers the whole push ----------------------

async def never_more() -> AsyncIterator[bytes]:
    yield b"{"
    await asyncio.Event().wait()


async def test_a_sender_that_stalls_after_one_byte_is_cut_at_the_deadline_and_frees_its_slot() -> None:
    adm = A.Admission(read_slots=1, grace_s=0.2, min_rate_bps=1000, max_read_s=1.0, slot_wait_s=0.1)
    started = asyncio.get_running_loop().time()
    with pytest.raises(PushRefusal) as e:
        await adm.read_body({"content-type": "application/json", "content-length": "1000"}, never_more())
    waited = asyncio.get_running_loop().time() - started
    assert (e.value.status, e.value.reason) == (408, "BODY_TIMEOUT") and 0.15 < waited < 1.0, waited

    async def ok() -> AsyncIterator[bytes]:
        yield b"{}"

    assert await adm.read_body({"content-type": "application/json"}, ok()) == b"{}"  # the slot came back


async def test_a_stall_without_a_content_length_is_cut_by_the_rate_rule_not_only_the_cap() -> None:
    adm = A.Admission(grace_s=0.2, min_rate_bps=1000, max_read_s=60.0)
    started = asyncio.get_running_loop().time()
    with pytest.raises(PushRefusal) as e:
        await adm.read_body({"content-type": "application/json"}, never_more())
    assert e.value.reason == "BODY_TIMEOUT" and asyncio.get_running_loop().time() - started < 2.0


async def test_four_stalled_senders_do_not_starve_the_next_push_for_long() -> None:
    adm = A.Admission(read_slots=4, grace_s=0.2, min_rate_bps=1000, max_read_s=1.0, slot_wait_s=3.0)
    stalled = [asyncio.create_task(adm.read_body({"content-type": "application/json", "content-length": "1000"}, never_more()))
               for _ in range(4)]
    await asyncio.sleep(0.05)

    async def ok() -> AsyncIterator[bytes]:
        yield b"{}"

    assert await adm.read_body({"content-type": "application/json"}, ok()) == b"{}"
    for t in stalled:
        with pytest.raises(PushRefusal):
            await t


def make_request(user: str = "writer") -> MagicMock:
    async def stream() -> AsyncIterator[bytes]:
        yield raw(envelope())

    request = MagicMock()
    request.state.user = {"userId": user, "orgId": "org1"}
    request.headers = JSON
    request.stream = stream
    return request


async def test_one_push_at_a_time_per_caller_covers_validation_and_write(h) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_push(**kw) -> PushResult:
        started.set()
        await release.wait()
        return PushResult("created", 201, "rid", "kb1", "k", 1, True, "abc", None, "newRecord", "QUEUED")

    svc = h.service()
    svc.push = slow_push
    h.service = lambda: svc
    first = asyncio.create_task(R.push_record("kb1", make_request(), None, MagicMock(), MagicMock()))
    await started.wait()  # the body is read; the push is validating or writing
    second = await R.push_record("kb1", make_request(), None, MagicMock(), MagicMock())
    assert second.status_code == 429 and json.loads(second.body)["error"]["reason"] == "PUSH_BUSY"
    other = h.service()
    assert other is svc
    release.set()
    assert (await first).status_code == 201
    started.clear()
    release.set()
    assert (await R.push_record("kb1", make_request(), None, MagicMock(), MagicMock())).status_code == 201  # released


async def test_the_callers_slot_is_released_when_the_body_is_refused(h) -> None:
    request = make_request()
    request.headers = {"content-type": "text/plain"}
    assert (await R.push_record("kb1", request, None, MagicMock(), MagicMock())).status_code == 415
    assert h.admission._in_flight == set()


async def test_a_cancelled_request_releases_the_slot_only_when_its_push_ends(h) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_push(**kw) -> PushResult:
        started.set()
        await release.wait()
        return PushResult("created", 201, "rid", "kb1", "k", 1, True, "abc", None, "newRecord", "QUEUED")

    svc = h.service()
    svc.push = slow_push
    h.service = lambda: svc
    task = asyncio.create_task(R.push_record("kb1", make_request(), None, MagicMock(), MagicMock()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.admission._in_flight == {"key-writer"}  # the push is still running
    release.set()
    await asyncio.sleep(0.05)
    assert h.admission._in_flight == set()


# ---- slots always come back; a disconnect mid-body is a client error -----------------------------

async def test_a_client_disconnect_mid_body_is_a_400_not_a_logged_500(h, caplog) -> None:
    from starlette.requests import ClientDisconnect

    async def broken() -> AsyncIterator[bytes]:
        yield b"{"
        raise ClientDisconnect

    request = make_request()
    request.stream = broken
    with caplog.at_level("ERROR"):
        response = await R.push_record("kb1", request, None, MagicMock(), MagicMock())
    assert response.status_code == 400 and json.loads(response.body)["error"]["reason"] == "CLIENT_DISCONNECTED"
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    assert h.admission._in_flight == set() and not h.pushed


@pytest.mark.parametrize("mode", ["ok", "death", "refused", "bug"])
async def test_the_slots_and_the_claim_come_back_after_every_outcome(h, mode) -> None:
    async def push(**kw) -> PushResult:
        if mode == "death":
            raise PushRefusal(503, "PUSH_TIMEOUT", "The validation worker stopped.")
        if mode == "refused":
            raise PushRefusal(422, "BLOCKS_INVALID", "x")
        if mode == "bug":
            raise RuntimeError("bug")
        return PushResult("created", 201, "rid", "kb1", "k", 1, True, "abc", None, "newRecord", "QUEUED")

    svc = h.service()
    svc.push = push
    h.service = lambda: svc
    response = await R.push_record("kb1", make_request(), None, MagicMock(), MagicMock())
    assert response.status_code == {"ok": 201, "death": 503, "refused": 422, "bug": 500}[mode]
    await asyncio.sleep(0.02)
    assert h.admission._in_flight == set()
    assert h.admission._slots._value == A.READ_SLOTS


@pytest.mark.parametrize("route", ["put", "get"])
async def test_schema_routes_refuse_a_request_with_no_bound_org(h, route: str) -> None:
    """A schema saved under no org could never match a push; the routes refuse as the push route does."""
    token = org_context.push(None)
    try:
        request = MagicMock()
        request.state.user = {"userId": "owner", "orgId": "org1"}
        call = R.put_kb_schema("kb1", request, MagicMock(), MagicMock()) if route == "put" else R.get_kb_schema("kb1", request, None, MagicMock(), MagicMock())
        response = await call
    finally:
        org_context.pop(token)

    assert response.status_code == 400
    assert json.loads(response.body)["error"]["reason"] == "ENVELOPE_INVALID"


def test_a_schema_body_over_the_limit_is_refused_before_it_is_parsed(client, h, monkeypatch) -> None:
    h.user = "owner"
    monkeypatch.setattr(A, "MAX_SCHEMA_BYTES", 100)
    body = json.dumps({"properties": [{"name": "p", "type": "string", "displayLabel": "x" * 200}]})

    declared = client.put("/api/v1/kb/kb1/schema", content=body, headers=JSON)
    streamed = client.put("/api/v1/kb/kb1/schema", content=iter([body[:60].encode(), body[60:].encode()]), headers=JSON)

    for r in (declared, streamed):
        assert r.status_code == 413 and error(r)["reason"] == "PAYLOAD_TOO_LARGE"


async def test_a_schema_body_that_stalls_is_cut_at_the_read_deadline() -> None:
    async def stalls() -> AsyncIterator[bytes]:
        yield b'{"properties":'
        await asyncio.sleep(3600)

    with pytest.raises(PushRefusal) as e:
        await A.read_bounded({}, stalls(), 1000, timeout_s=0.1)

    assert (e.value.status, e.value.reason) == (408, "BODY_TIMEOUT")


@pytest.mark.parametrize("declared", ["²", "١٠٠٠٠"])
async def test_a_content_length_in_non_ascii_digits_is_ignored_and_the_body_still_counted(declared: str) -> None:
    """int() raises on some of them and reads others as a number; the read must not trust either."""
    async def body() -> AsyncIterator[bytes]:
        yield b'{"properties": {}}'

    assert await A.read_bounded({"content-length": declared}, body(), 1000) == b'{"properties": {}}'

    async def too_big() -> AsyncIterator[bytes]:
        yield b"x" * 1001

    with pytest.raises(PushRefusal) as e:
        await A.read_bounded({"content-length": declared}, too_big(), 1000)
    assert (e.value.status, e.value.reason) == (413, "PAYLOAD_TOO_LARGE")
