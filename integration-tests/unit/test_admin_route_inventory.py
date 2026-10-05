"""The admin-route table matches the admin checks in the source.

``permissions/test_admin_routes.py`` proves each row of
``helper/admin_route_table.py`` refuses a member, but only the rows it is given.
A route that gains ``userAdminCheck`` (or a Python handler that gains an admin
check) without a row would never be called as a member, and a later change that
dropped its check would go unnoticed. These tests read the source and fail
until the table names every such route.

Source only, no stack, so they run on every pull request with the other helper
unit tests.
"""

from __future__ import annotations

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
