"""Every way into the agent loop applies the identified-caller guard.

A route, or any other code, that starts the loop without the guard would run a
client_credentials app or a service token with someone's access. This works from
structure, not from a list of known routes:

* the route tables of every service app (every `app/**/*_main.py`, as built for
  this edition, mounted sub-apps included) are walked, and every route whose
  endpoint can reach the loop must carry `require_identified_caller`;
* no route endpoint may be opaque to the scan (a lambda, a partial, a closure);
* the whole source tree is scanned, and any function, or module-level statement,
  that can reach the loop (directly or through any chain of callers, in any
  module) must be a guarded route or be on the reviewed allowlist below.

The synthetic modules in `agent_loop_entry_fixtures/` are shapes the scan must
flag.

What the scan follows and what it cannot see is listed in the header of
`agent_loop_entry_scan.py`. In short, `importlib` and other dynamic imports,
`getattr` with a computed name, and calls through objects whose class the scan
cannot see are declared limits; a handler mounted on an existing loop path is
still caught by the route table, whatever way it calls in. This test is the early
warning: the loop itself refuses a run the route guard did not verify
(`test_agent_loop_sink_guard.py`), so a shape it misses still fails closed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi import APIRouter, Depends, FastAPI

from app.api.middlewares.identified_caller import (
    refuse_unidentified_caller,
    require_identified_caller,
)
from tests.unit.api.agent_loop_entry_scan import (
    APP_ROOT,
    MODULE_LEVEL,
    build_graph,
    read_tree,
    route_rows,
    service_main_names,
    violations,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tests.unit.api.agent_loop_entry_scan import Graph

FIXTURES = Path(__file__).parent / "agent_loop_entry_fixtures"
# Each service app is imported in its own short-lived child process, one after the other, so the
# peak memory is the largest single app (not all of them at once, and never inside the test process,
# where the apps would also install signal handlers and set log names). A cold run of all of them
# takes about a minute on 3 cores.
SERVICE_APPS_TIMEOUT = 420
CHILD_TIMEOUT = 180

# Functions that reach the loop and are not routes themselves. Each is reviewed: it is reachable
# only from routes that carry the guard.
ALLOWLIST = {
    "app.api.routes.chatbot:_generate_chat_stream_via_agent_loop": "only called by the assistant chat handlers, which are guarded routes",
    "app.api.routes.chatbot:_stream_chat_via_agent_loop": "only called by _generate_chat_stream_via_agent_loop",
}


@pytest.fixture(scope="session")
def source_graph() -> Graph:
    return build_graph(read_tree())


def _import_one_main(name: str) -> dict:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(None, [str(APP_ROOT.parent), os.environ.get("PYTHONPATH")])
        ),
    }
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "routes.json"
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.unit.api.agent_loop_entry_scan",
                name,
                str(out),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=CHILD_TIMEOUT,
            check=False,
        )
        assert done.returncode == 0, (
            f"could not build the service app {name}:\n{done.stderr[-4000:]}"
        )
        return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def service_apps() -> dict:
    """{'mains': {main: (has an app, NO_HTTP_APP reason)}, 'middleware': {main: [names]},
    'rows': [route rows]} for every `app/**/*_main.py`, built once per session."""
    mains: dict[str, tuple[bool, str | None]] = {}
    middleware: dict[str, list[str]] = {}
    rows: list[dict] = []
    for name in service_main_names():
        found = _import_one_main(name)
        mains[name] = (found["has_app"], found["no_http_app"])
        middleware[name] = found["middleware"]
        rows.extend(found["rows"])
    assert (
        mains.get("query_main", (False,))[0]
        and mains.get("connectors_main", (False,))[0]
    ), mains
    return {"mains": mains, "middleware": middleware, "rows": tuple(rows)}


@pytest.fixture(scope="session")
def service_routes(service_apps: dict) -> tuple[dict, ...]:
    return service_apps["rows"]


def _unguarded_routes(rows: Iterable[dict], reaching: set[str]) -> list[str]:
    return sorted(
        f"{row['methods']} {row['path']} ({row['endpoint']})"
        for row in rows
        if row["endpoint"] in reaching and not row["guarded"]
    )


@pytest.mark.timeout(SERVICE_APPS_TIMEOUT)
def test_every_route_of_every_service_that_can_reach_the_agent_loop_has_the_guard(
    source_graph: Graph, service_routes: tuple[dict, ...]
) -> None:
    graph, routes = source_graph, service_routes
    found = {r["endpoint"] for r in routes} & graph.reaching
    # The scan must keep finding the known entries, or a rename would silently empty it.
    names = {key.split(":")[1] for key in found}
    assert {"chat", "chat_stream", "askAI", "askAIStream"} <= names, found
    assert not _unguarded_routes(routes, graph.reaching)


@pytest.mark.timeout(SERVICE_APPS_TIMEOUT)
def test_every_service_that_serves_the_loop_gives_each_request_its_own_trace_context(
    source_graph: Graph, service_apps: dict
) -> None:
    # The loop's own check binds a verification to the request's trace context, so a value left
    # behind on a reused task never admits a later request.
    serving = {
        r["service"]
        for r in service_apps["rows"]
        if r["endpoint"] in source_graph.reaching
    }
    assert "query_main" in serving, serving
    missing = sorted(
        n
        for n in serving
        if "RequestContextMiddleware" not in service_apps["middleware"][n]
    )
    assert not missing, (
        f"services that run the agent loop without RequestContextMiddleware: {missing}"
    )


@pytest.mark.timeout(SERVICE_APPS_TIMEOUT)
def test_no_route_of_any_service_has_an_endpoint_the_scan_cannot_see_into(
    service_routes: tuple[dict, ...],
) -> None:
    # A lambda, partial, closure or callable object as an endpoint hides what it calls from the scan.
    opaque = sorted(
        f"{r['methods']} {r['path']} ({r['endpoint']})"
        for r in service_routes
        if r["opaque"] and r["endpoint"].startswith("app.")
    )
    assert not opaque, (
        f"route endpoints the scan cannot follow: {opaque}. Register a named function instead."
    )


@pytest.mark.timeout(SERVICE_APPS_TIMEOUT)
def test_every_handler_on_a_loop_route_path_is_found_by_the_scan(
    source_graph: Graph, service_routes: tuple[dict, ...]
) -> None:
    # A wrapper mounted on the same method and path as a loop route is a loop route too. If the scan
    # cannot follow how it calls the wrapped handler, it would be skipped silently; this names it.
    graph, routes = source_graph, service_routes
    loop_paths = {
        (m, r["path"])
        for r in routes
        if r["endpoint"] in graph.reaching
        for m in r["methods"]
    }
    missed = sorted(
        f"{r['methods']} {r['path']} ({r['endpoint']})"
        for r in routes
        if r["endpoint"] not in graph.reaching
        and any((m, r["path"]) in loop_paths for m in r["methods"])
    )
    assert not missed, (
        f"handlers on an agent-loop route path that the scan does not see reaching the loop: {missed}"
    )


@pytest.mark.timeout(SERVICE_APPS_TIMEOUT)
def test_nothing_else_can_reach_the_agent_loop_without_being_a_guarded_route_or_reviewed(
    source_graph: Graph, service_routes: tuple[dict, ...]
) -> None:
    graph = source_graph
    guarded = {r["endpoint"] for r in service_routes if r["guarded"]}
    leftover = violations(graph, guarded, ALLOWLIST)
    assert not leftover, (
        "These functions can start the agent loop but are not a guarded route of any service app "
        f"and are not on the allowlist: {leftover}. Guard the route with require_identified_caller, "
        "or review the function and add it to ALLOWLIST with a reason."
    )


@pytest.mark.timeout(SERVICE_APPS_TIMEOUT)
def test_a_route_decorated_anywhere_that_reaches_the_loop_is_mounted_in_a_service_app(
    source_graph: Graph, service_routes: tuple[dict, ...]
) -> None:
    graph = source_graph
    mounted = {r["endpoint"] for r in service_routes}
    unmounted = sorted((graph.decorated_routes & graph.reaching) - mounted)
    assert not unmounted, (
        f"routes that reach the agent loop but are not in any service app: {unmounted}"
    )


def test_the_allowlist_has_no_stale_entries(source_graph: Graph) -> None:
    graph = source_graph
    assert set(ALLOWLIST) <= graph.reaching, sorted(set(ALLOWLIST) - graph.reaching)


def test_every_module_in_the_tree_parses(source_graph: Graph) -> None:
    # A module the scan cannot parse would be invisible to it, so it is a failure, never a skip.
    assert not source_graph.parse_errors, source_graph.parse_errors


@pytest.mark.timeout(SERVICE_APPS_TIMEOUT)
def test_every_service_main_builds_an_app_or_says_why_not(service_apps: dict) -> None:
    # A main that stopped exposing a module-level `app` (say, a `create_app()` factory) would drop
    # out of the route checks; it must build one, or say in `NO_HTTP_APP` why it serves no routes.
    mains = service_apps["mains"]
    problems = {
        "no app and no NO_HTTP_APP reason": sorted(
            n for n, (has, why) in mains.items() if not has and not why
        ),
        "an app and a NO_HTTP_APP reason": sorted(
            n for n, (has, why) in mains.items() if has and why
        ),
    }
    assert not any(problems.values()), problems


def test_service_mains_are_found_at_any_depth(tmp_path: Path) -> None:
    for rel in (
        "query_main.py",
        "edition/sync_main.py",
        "edition/deep/x_main.py",
        "edition/helpers.py",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    assert service_main_names(tmp_path) == [
        "edition.deep.x_main",
        "edition.sync_main",
        "query_main",
    ]


@pytest.mark.parametrize("kind", ["app", "service", "service_account"])
def test_the_guard_refuses_each_principal_without_a_person(kind: str) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        refuse_unidentified_caller(
            {"userId": "u1", "orgId": "o1", "principalKind": kind}
        )
    assert exc.value.status_code == 403
    assert "personal access token" in exc.value.detail


# --- mutants and controls: the scan must flag every one ----------------------------------------------

BASE = [
    "fx.agents.chat_modes",
    "fx.agents.stream_bridge",
    "fx.api.routes.chatbot",
    "fx.api.routes.agent",
]
FX_ALLOW = {"fx.api.routes.chatbot:_generate": "called only by guarded handlers"}
GUARDED_FX = {"fx.api.routes.chatbot:askAIStream", "fx.api.routes.agent:chat_stream"}


def _fixture_sources(*names: str) -> dict[str, bytes]:
    # Bytes, as `read_tree` gives them, so a fixture saved with a BOM is read the same way.
    sources: dict[str, bytes] = {}
    for name in BASE:
        sources[name] = (FIXTURES / f"{name}.pytxt").read_bytes()
    for name in names:
        path = next(FIXTURES.glob(f"{name}.*.pytxt"))
        module = path.name.removeprefix(f"{name}.").removesuffix(".pytxt")
        sources[module] = path.read_bytes()
    return sources


def _flagged(*names: str, guarded: set[str] | None = None) -> list[str]:
    return violations(build_graph(_fixture_sources(*names)), guarded or set(), FX_ALLOW)


def test_the_unmodified_fixture_tree_is_clean_when_its_routes_are_guarded() -> None:
    # chat_stream and askAIStream are the fixture's guarded routes
    assert _flagged(guarded=GUARDED_FX) == []


@pytest.mark.parametrize(
    ("scenarios", "expected"),
    [
        # a new route in the same module, an edition wrapper, a relative import
        (("control_same_module_route",), "fx.api.routes.chatbot:new_unguarded"),
        (("control_edition_wrapper",), "fx.edition.api.routes.agent:new_route"),
        (("control_relative_import",), "fx.services.relpkg:job"),
        # a new router module, an edition module, a service job, two hops away
        (("missed_new_router_module",), "fx.api.routes.extra:qa_chat"),
        (
            ("missed_edition_module_calls_run_chat_stream",),
            "fx.edition.api.routes.extra:qa_chat",
        ),
        (("missed_service_job_calls_chat_stream",), "fx.services.job:nightly"),
        (
            ("missed_service_job_calls_chat_stream", "missed_two_hops"),
            "fx.services.indirect:weekly",
        ),
        # other ways to name a wrapper: a re-export, a dotted import, a module-level alias, a
        # relative import in a package `__init__`
        (
            ("missed_reexport_module", "missed_reexport_caller"),
            "fx.services.reexport_job:job",
        ),
        (("missed_dotted_import",), "fx.services.dotted:job"),
        (("missed_assigned_alias",), "fx.services.assigned:job"),
        (
            ("missed_package_init_relative_import", "control_relative_import"),
            "fx.services:package_job",
        ),
        # control: a function that replaces an imported name is still reported, not skipped as a re-export
        (
            ("control_definition_shadows_an_import",),
            "fx.edition.api.routes.shadow:chat_stream",
        ),
        # a module-level lambda or partial, a constant getattr, a BOM, a def inside `try`
        (("missed_module_level_lambda",), "fx.services.lam:job"),
        (("missed_module_level_partial",), "fx.services.part:job"),
        (("missed_getattr_constant",), "fx.services.ga:job"),
        (("missed_utf8_bom",), "fx.services.bom:job"),
        (("missed_def_inside_try",), "fx.services.tryblock:job"),
        # a method named like the guarded `chat_stream` in the same module is keyed by its class
        (
            ("missed_method_shares_a_route_name",),
            "fx.api.routes.agent:Runner.chat_stream",
        ),
        (
            (
                "missed_method_shares_a_route_name",
                "missed_method_shares_a_route_name_caller",
            ),
            "fx.services.runner_job:job",
        ),
        (("missed_self_method_dispatch",), "fx.services.selfcall:nightly"),
        # a function-local import of the same name does not hide the module-level one
        (("missed_local_import_shadows_module_import",), "fx.services.localimp:job"),
        # a call in a module-level assignment runs at import
        (("missed_module_level_assigned_call",), f"fx.services.initcall:{MODULE_LEVEL}"),
        # a route registered by a module-level statement, with a lambda calling the handler
        (
            ("missed_module_level_add_api_route_lambda",),
            f"fx.api.routes.lamroute:{MODULE_LEVEL}",
        ),
    ],
)
def test_each_mutant_and_control_is_flagged(
    scenarios: tuple[str, ...], expected: str
) -> None:
    flagged = _flagged(*scenarios, guarded=GUARDED_FX)
    assert expected in flagged, flagged


def test_a_module_that_does_not_parse_is_reported_not_dropped() -> None:
    graph = build_graph({**_fixture_sources(), "fx.services.broken": b"def job(:\n"})
    assert set(graph.parse_errors) == {"fx.services.broken"}, graph.parse_errors


def test_module_level_aliases_are_followed_but_not_reported_themselves() -> None:
    for scenario, expected in [
        ("missed_module_level_lambda", ["fx.services.lam:job"]),
        ("missed_module_level_partial", ["fx.services.part:job"]),
    ]:
        assert _flagged(scenario, guarded=GUARDED_FX) == expected


def test_a_re_export_is_followed_but_not_reported_itself() -> None:
    flagged = _flagged(
        "missed_reexport_module", "missed_reexport_caller", guarded=GUARDED_FX
    )
    assert flagged == ["fx.services.reexport_job:job"], flagged


def test_every_fixture_file_is_used() -> None:
    used = {
        "control_same_module_route",
        "control_edition_wrapper",
        "control_relative_import",
        "missed_new_router_module",
        "missed_edition_module_calls_run_chat_stream",
        "missed_service_job_calls_chat_stream",
        "missed_two_hops",
        "missed_reexport_module",
        "missed_reexport_caller",
        "missed_dotted_import",
        "missed_local_import_shadows_module_import",
        "missed_module_level_assigned_call",
        "missed_assigned_alias",
        "missed_package_init_relative_import",
        "control_definition_shadows_an_import",
        "missed_module_level_lambda",
        "missed_module_level_partial",
        "missed_getattr_constant",
        "missed_utf8_bom",
        "missed_def_inside_try",
        "missed_method_shares_a_route_name",
        "missed_method_shares_a_route_name_caller",
        "missed_self_method_dispatch",
        "missed_module_level_add_api_route_lambda",
    }
    on_disk = {
        p.name.split(".", 1)[0]
        for p in FIXTURES.glob("*.pytxt")
        if not p.name.startswith("fx.")
    }
    assert on_disk == used, on_disk ^ used


def test_a_route_without_the_guard_is_found_in_a_route_table() -> None:
    """Control for 'the guard was removed from a route': the route-table check names it."""

    async def askAIStream() -> None:
        return None

    askAIStream.__module__ = "fx.api.routes.chatbot"
    askAIStream.__qualname__ = "askAIStream"
    router = APIRouter()
    router.add_api_route("/chat", askAIStream, methods=["POST"])
    router.add_api_route(
        "/chat2",
        askAIStream,
        methods=["POST"],
        dependencies=[Depends(require_identified_caller)],
    )
    app = FastAPI()
    app.include_router(router)
    unguarded = _unguarded_routes(
        route_rows(app.routes), {"fx.api.routes.chatbot:askAIStream"}
    )
    assert unguarded == ["['POST'] /chat (fx.api.routes.chatbot:askAIStream)"], (
        unguarded
    )


def _loop_handler(name: str = "askAIStream"):  # noqa: ANN202 - a stand-in route handler
    async def handler() -> None:
        return None

    handler.__module__, handler.__qualname__ = "fx.api.routes.chatbot", name
    return handler


def test_a_handler_re_registered_in_a_mounted_sub_app_is_found_unguarded() -> None:
    from starlette.middleware.gzip import GZipMiddleware

    handler = _loop_handler()
    for wrap in (lambda sub: sub, GZipMiddleware):
        sub = FastAPI()
        sub.add_api_route("/chat/stream", handler, methods=["POST"])
        app = FastAPI()
        app.mount("/outer", wrap(sub))
        unguarded = _unguarded_routes(
            route_rows(app.routes), {"fx.api.routes.chatbot:askAIStream"}
        )
        assert unguarded == [
            "['POST'] /outer/chat/stream (fx.api.routes.chatbot:askAIStream)"
        ], unguarded


def test_a_guarded_router_included_into_a_mounted_sub_app_stays_guarded() -> None:
    router = APIRouter()
    router.add_api_route(
        "/chat/stream",
        _loop_handler(),
        methods=["POST"],
        dependencies=[Depends(require_identified_caller)],
    )
    sub = FastAPI()
    sub.include_router(router)
    app = FastAPI()
    app.mount("/outer", sub)
    assert not _unguarded_routes(
        route_rows(app.routes), {"fx.api.routes.chatbot:askAIStream"}
    )


def test_a_plain_starlette_route_is_a_row_and_never_guarded() -> None:
    app = FastAPI()
    app.add_route("/raw", _loop_handler(), methods=["POST"])
    rows = [r for r in route_rows(app.routes) if r["path"] == "/raw"]
    assert [(r["endpoint"], r["guarded"]) for r in rows] == [
        ("fx.api.routes.chatbot:askAIStream", False)
    ]


def test_a_lambda_a_partial_and_a_closure_endpoint_are_opaque() -> None:
    import functools

    handler = _loop_handler()

    def make() -> object:
        async def inner() -> None:
            return None

        return inner

    app = FastAPI()
    app.add_api_route("/lambda", lambda: handler(), methods=["POST"])
    app.add_api_route("/partial", functools.partial(handler), methods=["POST"])
    app.add_api_route("/closure", make(), methods=["POST"])
    app.add_api_route("/named", handler, methods=["POST"])
    opaque = {
        r["path"]: r["opaque"]
        for r in route_rows(app.routes)
        if r["path"] in {"/lambda", "/partial", "/closure", "/named"}
    }
    assert opaque == {
        "/lambda": True,
        "/partial": True,
        "/closure": True,
        "/named": False,
    }, opaque
    (partial_row,) = [r for r in route_rows(app.routes) if r["path"] == "/partial"]
    assert partial_row["endpoint"] == "fx.api.routes.chatbot:askAIStream"
