"""No route that reads records can forget the connection limit.

A connected app (an OAuth app or a personal access token) may be limited to
some connections. The limit rides on the request user, and a route applies it
by reading ``connection_limit`` (to bound a search or a turn) or by calling one
of the shared checks in ``app.api.connection_limit``, which read it for it.

This walks every mounted route, picks those that read records, and follows the
code each one runs (through the helpers it calls) looking for a use of the
limit. A route that reads records and neither applies it nor sits in
``NOT_LIMITED`` with a reason fails. Add the check to the route, never an
entry here, unless the route cannot be limited and you can say why.

This is a name check: it finds that a route reads the limit, not that it
enforces it, and its record-reading rule is heuristic. It is meant to be replaced by
a shared dependency that every record-reading route declares, checked on
``route.dependant``.
"""

import inspect
import re
import types
from collections.abc import Callable

import pytest
from fastapi.routing import APIRoute

import app.edition_config  # noqa: F401 - the routers load cleanly only after this
from app.api.middlewares.auth import connection_limit
from tests.unit.api.test_route_auth_policy import _mounted_routes, _policies

#: Reading the limit, directly or through a shared check that reads it.
APPLIES_THE_LIMIT = frozenset({
    "connection_limit",
    "within_connection",
    "ids_outside_connection",
    "ids_outside_connection_on_restore",
    "refuse_outside_connection",
    "groups_outside_limit",
    "connection_apps",
    "named_inside_connection",
    "keep_path_inside_connection",
})

Key = tuple[str, str, str]  # (router, method, path)

#: A route reads records when it admits a token for one of these scopes ...
RECORD_SCOPES = frozenset({
    "semantic:read", "semantic:write", "conversation:chat", "agent:execute", "code:read",
})
#: ... or when it is a read (GET or POST) whose path names records.
RECORD_PATH = re.compile(r"record|stream|knowledge-graph|knowledge-hub/nodes|/children|/graph/")
READ_METHODS = frozenset({"GET", "POST"})
#: Reads the rules above miss (a path that names no record).
EXTRA_READS: frozenset[Key] = frozenset({("kb", "GET", "/api/v1/kb/")})

_MAX_DEPTH = 6

#: Routes that read or touch records yet do not apply the limit, and why.
NOT_LIMITED: dict[Key, str] = {
    # Take no record of the knowledge base: they act on the caller's own upload, a running turn, or audio.
    ("chatbot", "POST", "/chat/attachments/upload"): "stores the caller's own upload; reads no connected record",
    ("chatbot", "DELETE", "/chat/attachments/{record_id}"): "deletes the caller's own upload",
    ("chatbot", "POST", "/chat/cancel"): "cancels the caller's own running turn",
    ("speech", "POST", "/chat/transcribe"): "audio in, text out; reads no record",
    ("speech", "POST", "/chat/speak"): "text in, audio out; reads no record",
    ("speech", "GET", "/chat/speech/capabilities"): "reports what the deployment supports",
    # Service-to-service: a service token carries no connection limit (it is never an OAuth/PAT token).
    ("chatbot", "POST", "/chat/records/access"): "service token only; carries no limit",
    # Not reads of a record's content.
    ("agent", "GET", "/{agent_id}/run-definition"): "an agent's settings; a limited token never starts a durable run",
    ("agent", "GET", "/{agent_id}/readiness"): "which of the agent's actions and MCP servers the caller can use; reads no record",
    ("connectors", "POST", "/api/v1/record/buffer/convert"): "converts a buffer the caller posts; reads no stored record",
    ("connectors", "POST", "/api/v1/records/{record_id}/reindex"): "a write: queues indexing",
    ("connectors", "POST", "/api/v1/record-groups/{record_group_id}/reindex"): "a write: queues indexing",
    ("connectors", "GET", "/api/v1/stats"): "counts only (follow-up: not bounded to the limit)",
    # Artifacts are conversation outputs kept in the artifact store, not connector or collection records.
    # Follow-up: decide whether a connected app's limit should reach them.
    ("artifacts", "GET", "/api/v1/artifacts"): "artifact store, not connection records (follow-up)",
    ("artifacts", "GET", "/api/v1/artifacts/{artifact_id}"): "artifact store, not connection records (follow-up)",
    ("artifacts", "GET", "/api/v1/artifacts/{artifact_id}/versions"): "artifact store, not connection records (follow-up)",
    ("artifacts", "GET", "/api/v1/artifacts/{artifact_id}/permissions"): "artifact store, not connection records (follow-up)",
    ("artifacts", "POST", "/api/v1/artifacts/copy"): "artifact store, not connection records (follow-up)",
}


def _code_names(code: types.CodeType) -> set[str]:
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


def applies_the_limit(fn: Callable[..., object], _seen: set | None = None, _depth: int = 0) -> bool:
    """Whether ``fn``, or an ``app`` function it calls (up to ``_MAX_DEPTH`` deep), reads the limit."""
    seen = set() if _seen is None else _seen
    fn = inspect.unwrap(fn)
    code = getattr(fn, "__code__", None)
    if code is None or fn in seen or _depth > _MAX_DEPTH:
        return False
    seen.add(fn)
    names = _code_names(code)
    if names & APPLIES_THE_LIMIT:
        return True
    scope = getattr(fn, "__globals__", {})
    return any(
        inspect.isfunction(target) and (target.__module__ or "").startswith("app.")
        and applies_the_limit(target, seen, _depth + 1)
        for target in (scope.get(n) for n in names)
    )


def _scopes(route: APIRoute) -> set[str]:
    return {str(getattr(s, "value", s)) for p in _policies(route) for s in p.oauth_scopes}


def _reads_records(route: APIRoute, key: Key) -> bool:
    if key in EXTRA_READS:
        return True
    if key[1] not in READ_METHODS:
        return False
    return bool(_scopes(route) & RECORD_SCOPES) or bool(RECORD_PATH.search(route.path))


def _routes() -> dict[Key, APIRoute]:
    return {
        (name, method, route.path): route
        for name, route in _mounted_routes()
        for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
    }


def test_the_walk_sees_the_routes_that_matter() -> None:
    """A walk that finds nothing would pass for the wrong reason."""
    keys = {k for k, r in _routes().items() if _reads_records(r, k)}
    for expected in (
        ("knowledge_search", "POST", "/search/grep"),
        ("knowledge_search", "POST", "/search/entities"),
        ("search", "POST", "/search"),
        ("chatbot", "POST", "/chat"),
        ("agent", "POST", "/{agent_id}/chat"),
        ("connectors", "GET", "/api/v1/records/{record_id}"),
        ("connectors", "GET", "/api/v1/stream/record/{record_id}"),
    ):
        assert expected in keys, expected
    assert len(keys) > 30


def test_the_detector_tells_a_limited_function_from_an_unlimited_one() -> None:
    def limited(request):  # noqa: ANN202
        return connection_limit(request.state.user)

    def through_a_helper(request):  # noqa: ANN202
        return limited(request)

    def unlimited(request):  # noqa: ANN202
        return request.state.user

    assert applies_the_limit(limited) and not applies_the_limit(unlimited)
    # Helpers defined in the test module are not under app., so only app code is followed.
    assert not applies_the_limit(through_a_helper)


def test_every_route_that_reads_records_applies_the_connection_limit() -> None:
    missing = sorted(
        key for key, route in _routes().items()
        if _reads_records(route, key) and key not in NOT_LIMITED and not applies_the_limit(route.endpoint)
    )
    assert not missing, (
        "These routes read records but never apply the connected app's limit. "
        "Bound them with connection_limit/within_connection or a check from "
        f"app.api.connection_limit: {missing}"
    )


@pytest.mark.parametrize("key", sorted(NOT_LIMITED))
def test_each_exemption_names_a_real_route_that_still_needs_it(key: Key) -> None:
    routes = _routes()
    assert key in routes, f"{key} is not mounted any more: remove it from NOT_LIMITED"
    assert NOT_LIMITED[key].strip()
    assert not applies_the_limit(routes[key].endpoint), f"{key} applies the limit now: remove it from NOT_LIMITED"
