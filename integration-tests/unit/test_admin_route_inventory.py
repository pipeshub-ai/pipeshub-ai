"""The admin-route table matches the admin checks in the source.

``permissions/test_admin_routes.py`` proves each row of
``helper/admin_route_table.py`` refuses a member, but only the rows it is given.
A route that gains ``userAdminCheck`` (or a Python handler that gains an admin
check) without a row would never be called as a member, and a later change that
dropped its check would go unnoticed. These tests read the source and fail
until the table names every such route.

A Python handler whose admin check depends on what is asked for cannot be
tested with one "member is refused" request. Those are listed with their rule
in ``PYTHON_CONDITIONAL_ADMIN``, and ``helper/conditional_admin_table.py`` holds
the live cases ``permissions/test_conditional_admin_routes.py`` sends for each.
The tests in the second half fail until every such handler has cases, and
until those cases could not pass for the wrong reason.

Source only, no stack, so they run on every pull request with the other helper
unit tests.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from helper.admin_route_table import (
    ADMIN_BODIES,
    NODE_ADMIN_ROUTES,
    PYTHON_ADMIN_ROUTES,
    PYTHON_BEHIND_NODE_ADMIN,
    PYTHON_CONDITIONAL_ADMIN,
)
from helper.admin_routes import (
    admin_routes_in_file,
    discover_admin_routes,
    discover_python_admin_handlers,
)
from helper.conditional_admin_table import (
    ADMIN,
    CALLERS,
    CONDITIONAL_ROUTES,
    CONNECTOR_SERVICE,
    DERIVED_KEYS,
    FRESH_KEYS,
    GATEWAY,
    MEMBER,
    REQUIRED_CASES,
    WORLD_GROUPS,
    WORLD_KEYS,
    Case,
    ConditionalRoute,
    fill,
    placeholders,
    whose,
)

pytestmark = pytest.mark.unit

# Guards against a parser that silently finds nothing: the source has well over
# this many of each today.
_MIN_NODE_ROUTES = 80
_MIN_PYTHON_HANDLERS = 40


def test_every_node_admin_route_has_a_row() -> None:
    found = {(r.method, r.path): r.source for r in discover_admin_routes()}
    assert len(found) >= _MIN_NODE_ROUTES, (
        f"Only {len(found)} admin routes were found in the Node source; the reader "
        "has probably stopped matching the router code."
    )
    listed = {r.key for r in NODE_ADMIN_ROUTES}
    missing = sorted(set(found) - listed)
    assert not missing, (
        "These Node routes require an org admin but are not in "
        "helper/admin_route_table.py, so no test checks that a member is refused:\n"
        + "\n".join(f"  {m} {p}  ({found[(m, p)]})" for m, p in missing)
    )
    stale = sorted(listed - set(found))
    assert not stale, (
        "These rows no longer match an admin-gated route in the source (renamed, "
        f"removed, or the admin check was dropped): {stale}"
    )


def test_every_python_admin_check_is_classified() -> None:
    found = discover_python_admin_handlers()
    assert len(found) >= _MIN_PYTHON_HANDLERS, (
        f"Only {len(found)} Python handlers with an admin check were found; the "
        "reader has probably stopped matching the route code."
    )
    tested = {r.handler for r in PYTHON_ADMIN_ROUTES}
    classified = tested | PYTHON_BEHIND_NODE_ADMIN | set(PYTHON_CONDITIONAL_ADMIN)
    missing = sorted(set(found) - classified)
    assert not missing, (
        "These Python handlers check for an org admin but are not in "
        "helper/admin_route_table.py. Add each to PYTHON_ADMIN_ROUTES if it refuses "
        "every member, or to PYTHON_CONDITIONAL_ADMIN with the reason if not:\n"
        + "\n".join(f"  {key}  ({found[key]})" for key in missing)
    )
    stale = sorted(classified - set(found))
    assert not stale, f"These rows name handlers with no admin check any more: {stale}"


def test_rows_are_complete() -> None:
    rows = NODE_ADMIN_ROUTES + PYTHON_ADMIN_ROUTES
    assert len({r.key for r in rows}) == len(rows), "a route is listed twice"
    no_reason = [r.id for r in rows if r.admin is None and not r.why_no_admin]
    assert not no_reason, f"rows that skip the admin call without saying why: {no_reason}"
    unknown = [r.id for r in rows if r.admin not in (None, "same", "invalid", "admin_body")]
    assert not unknown, f"rows with an unknown admin request kind: {unknown}"
    no_body = [r.id for r in rows if r.admin == "admin_body" and r.key not in ADMIN_BODIES]
    assert not no_body, f"rows that need an admin body but have none: {no_body}"


def test_a_malformed_admin_request_only_goes_where_a_validator_stops_it() -> None:
    """``invalid`` and ``same`` admin requests must not be able to write.

    An ``invalid`` request relies on a validator after the admin check to stop
    it. A ``same`` write relies on aiming at an id that does not exist, so its
    path must have one.
    """
    discovered = {(r.method, r.path): r for r in discover_admin_routes()}
    unguarded = [
        row.id for row in NODE_ADMIN_ROUTES
        if row.admin == "invalid"
        and "ValidationMiddleware.validate" not in discovered[row.key].after_admin
    ]
    assert not unguarded, (
        f"These rows send a malformed admin request, but nothing after the admin "
        f"check validates it, so it could be written: {unguarded}"
    )
    blind_writes = [
        row.id for row in NODE_ADMIN_ROUTES + PYTHON_ADMIN_ROUTES
        if row.admin == "same" and row.method != "GET" and ":" not in row.path
    ]
    assert not blind_writes, f"admin writes with no id to aim at: {blind_writes}"


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_the_reader_finds_inline_and_router_wide_admin_checks(tmp_path: Path) -> None:
    source = _write(tmp_path, "sample.routes.ts", """
        export function createSampleRouter(container: Container) {
          const router = Router();
          // router.get('/commented', userAdminCheck, handler)
          router.get('/open', authMiddleware.authenticate, handler);
          router.post(
            '/gated/:id',
            authMiddleware.authenticate,
            ValidationMiddleware.validate(schema),
            userAdminCheck,
            (req, res, next) => controller.go(req, res, next),
          );
          router.use(userAdminCheck);
          router.delete('/after-use', (req, res) => res.json({ ok: 'a, b' }));
          return router;
        }
    """)
    routes = admin_routes_in_file(source, "/api/v1/sample")
    got = {(r.method, r.path): r.before_admin for r in routes}
    assert set(got) == {("POST", "/api/v1/sample/gated/:id"), ("DELETE", "/api/v1/sample/after-use")}
    assert got[("POST", "/api/v1/sample/gated/:id")] == (
        "authMiddleware.authenticate", "ValidationMiddleware.validate",
    )


def _conditional_rows() -> dict[str, ConditionalRoute]:
    return {route.handler: route for route in CONDITIONAL_ROUTES}


def test_every_conditional_admin_handler_has_live_cases() -> None:
    rows = _conditional_rows()
    assert len(rows) == len(CONDITIONAL_ROUTES), "a handler has two rows of live cases"
    missing = sorted(set(PYTHON_CONDITIONAL_ADMIN) - set(rows))
    assert not missing, (
        "These handlers are in PYTHON_CONDITIONAL_ADMIN but have no row in "
        "helper/conditional_admin_table.py, so nothing checks on a running stack "
        "that they enforce their rule:\n"
        + "\n".join(f"  {key}  ({PYTHON_CONDITIONAL_ADMIN[key]})" for key in missing)
    )
    stale = sorted(set(rows) - set(PYTHON_CONDITIONAL_ADMIN))
    assert not stale, f"These rows of live cases name handlers that are no longer conditional: {stale}"
    empty = sorted(key for key, route in rows.items() if not route.cases)
    assert not empty, f"These rows have no cases: {empty}"


def test_each_conditional_row_calls_the_route_its_handler_serves() -> None:
    """The gateway may serve a handler under a longer path; the end of it must match."""
    served = discover_python_admin_handlers()
    wrong = []
    for handler, route in _conditional_rows().items():
        method, _, path = served[handler].partition(" ")
        ending = re.sub(r"\\\{\w+\\\}", "[^/]+", re.escape(path.removeprefix("/api/v1"))) + "$"
        called = re.sub(r"\{\w+\}", "x", route.path)
        if route.method != method or not re.search(ending, called):
            wrong.append(f"{handler} serves {served[handler]}; its row calls {route.id}")
    assert not wrong, "Rows that call a different route than the handler they name:\n  " + "\n  ".join(wrong)


def _every_case() -> list[tuple[ConditionalRoute, Case]]:
    return [(route, case) for route in CONDITIONAL_ROUTES for case in route.cases]


def test_conditional_cases_are_well_formed() -> None:
    twice = sorted(
        route.id for route in CONDITIONAL_ROUTES
        if len({case.id for case in route.cases}) != len(route.cases)
    )
    assert not twice, f"rows with the same (target, caller) case twice: {twice}"
    strangers = sorted({case.caller for _, case in _every_case()} - CALLERS)
    assert not strangers, f"cases with an unknown caller: {strangers}"
    elsewhere = sorted(r.id for r in CONDITIONAL_ROUTES if r.service not in (GATEWAY, CONNECTOR_SERVICE))
    assert not elsewhere, f"rows served by an unknown service: {elsewhere}"
    silent = sorted(
        f"{route.id} [{case.id}]" for route, case in _every_case()
        if not case.allowed and not case.words and not case.absent
    )
    assert not silent, f"refused cases that pin nothing about the refusal: {silent}"


def test_every_placeholder_is_something_the_test_world_holds() -> None:
    """A misspelt ``{key}`` would otherwise be sent as it is written."""
    owners = [group for group, keys in WORLD_GROUPS.items() for _ in keys]
    assert len(WORLD_KEYS) == len(owners), "a key belongs to two groups of the test world"
    assert not (WORLD_KEYS & (FRESH_KEYS | DERIVED_KEYS)), "a key is both made once and made fresh"
    known = WORLD_KEYS | FRESH_KEYS
    unknown = []
    for route, case in _every_case():
        used = placeholders([
            route.path, case.words, case.absent,
            route.params if case.params is None else case.params,
            route.body if case.body is None else case.body,
        ])
        problems = sorted(used - known - DERIVED_KEYS)
        if used & DERIVED_KEYS and case.target not in known:
            problems.append(f"target {case.target}")
        if "target_name" in used and f"{case.target}_name" not in known:
            problems.append(f"{case.target}_name")
        if problems:
            unknown.append(f"{route.id} [{case.id}]: {problems}")
    assert not unknown, "Cases that use something the test world does not hold:\n  " + "\n  ".join(unknown)


def _told_apart(allowed: Case, refused: Case) -> bool:
    """Could one reply satisfy both? Not if the statuses differ, or one case
    requires what the other forbids."""
    if allowed.status != refused.status:
        return True
    return bool(
        (refused.words and refused.words in allowed.absent)
        or (allowed.words and allowed.words in refused.absent)
    )


def test_no_conditional_case_can_pass_for_the_wrong_reason() -> None:
    """A member's 404, or a list without the connector in it, is also what a
    missing target gives. So a refusal that could mean "not there" needs an
    allowed case on the same target, and no reply may satisfy both."""
    problems = []
    for route in CONDITIONAL_ROUTES:
        allowed = [case for case in route.cases if case.allowed]
        refused = [case for case in route.cases if not case.allowed]
        if route.same_for_everyone:
            by_caller = {case.caller: (case.status, case.words, case.absent) for case in allowed}
            if refused or by_caller.get(MEMBER) is None or by_caller.get(MEMBER) != by_caller.get(ADMIN):
                problems.append(
                    f"{route.id} is marked the same for everyone, so it needs a member case and "
                    "an admin case that expect the same reply, and no refusal"
                )
            continue
        if not allowed or not refused:
            problems.append(f"{route.id} needs a refused case and an allowed one")
        for case in refused:
            could_be_missing = case.status == 404 or case.status < 300
            if could_be_missing and not any(a.target == case.target for a in allowed):
                problems.append(
                    f"{route.id} [{case.id}] would pass if {case.target!r} did not exist: "
                    "no allowed case uses the same target"
                )
            problems.extend(
                f"{route.id}: one reply could satisfy both [{case.id}] and [{a.id}]"
                for a in allowed if not _told_apart(a, case)
            )
    assert not problems, "\n".join(problems)


def test_connector_rules_cover_every_kind_of_caller() -> None:
    """Whose connector it is and who is asking decide these rules, so each
    combination the rule tells apart needs its own case."""
    rows = _conditional_rows()
    gaps = []
    for handler, rule in PYTHON_CONDITIONAL_ADMIN.items():
        required = REQUIRED_CASES.get(rule)
        if required is None:
            continue
        covered = {(whose(case.target), case.caller, case.allowed) for case in rows[handler].cases}
        gaps.extend(f"{handler} ({rule}): no case for {miss}" for miss in sorted(required - covered))
    assert not gaps, "\n".join(gaps)
    unused = sorted(set(REQUIRED_CASES) - set(PYTHON_CONDITIONAL_ADMIN.values()))
    assert not unused, f"REQUIRED_CASES names rules no handler has any more: {unused}"


def test_the_test_world_can_make_every_group() -> None:
    from helper.conditional_admin_world import World  # noqa: PLC0415 - only this test needs it

    missing = sorted(g for g in WORLD_GROUPS if not callable(getattr(World, f"_build_{g}", None)))
    assert not missing, f"groups of the test world with nothing to make them: {missing}"


def test_placeholders_are_found_and_filled_at_any_depth() -> None:
    value = {"a": "{one}/x", "b": [{"c": ("{two}", 3)}], "d": None}
    assert placeholders(value) == {"one", "two"}
    assert fill(value, str.upper) == {"a": "ONE/x", "b": [{"c": ("TWO", 3)}], "d": None}
