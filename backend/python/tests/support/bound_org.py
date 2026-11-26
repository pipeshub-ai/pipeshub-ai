"""An autouse fixture body for tests of code that acts for one org through ``app.core.org_context``."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app.core import org_context

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

TEST_ORG = "org-1"


def bind_test_org(org_id: str = TEST_ORG) -> Iterator[str]:
    token = org_context.push(org_context.OrgContext(org_id, None, "job", "job:test", "-"))
    try:
        yield org_id
    finally:
        org_context.pop(token)


@contextmanager
def bound_like_auth(user: Mapping[str, Any] | None) -> Iterator[None]:
    """Binds what ``authMiddleware`` binds for ``user`` (request.state.user): its org and person, or nothing for a
    user with no org. For test stand-ins of the middleware and tests that call a route function directly."""
    org_id = (user or {}).get("orgId")
    if not org_id:
        yield
        return
    scoped = (user or {}).get("token_type") == "scoped"
    ctx = org_context.OrgContext(
        org_id, (user or {}).get("userId"), "service" if scoped else "user", "scoped_jwt" if scoped else "jwt", "-"
    )
    with org_context.bind(ctx):
        yield


@contextmanager
def another_request(org_id: str | None, user_id: str | None = None) -> Iterator[None]:
    """A request by a caller of ``org_id`` (None: a token with no org) inside a test bound to another org: its own
    binding, as the auth middleware gives each request, never one nested in the test's (which would refuse)."""
    cleared = org_context.push(None)
    try:
        if org_id is None:
            yield
            return
        with org_context.bind(org_context.OrgContext(org_id, user_id, "user", "jwt", "-")):
            yield
    finally:
        org_context.pop(cleared)


def authed[**P, R](route: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """``route`` called as an authenticated request reaches it: bound as ``authMiddleware`` binds the verified claims
    the route takes (its ``claims`` argument, else its last), for a service-token route a test calls directly."""

    async def call(*args: P.args, **kwargs: P.kwargs) -> R:
        claims = kwargs.get("claims", args[-1] if args else None)
        with bound_like_auth(claims if isinstance(claims, Mapping) else None):
            return await route(*args, **kwargs)

    return call
