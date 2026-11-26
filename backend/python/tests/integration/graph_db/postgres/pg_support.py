"""Helpers for the graph tests: a provider bound to one org."""
from __future__ import annotations

import secrets
from typing import Any

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.provider import PostgresGraphProvider


def new_org() -> str:
    return secrets.token_hex(12)


class OrgBound:
    """Every async call of the provider runs in the ambient org, as a request would (a fixture's context does not reach the test)."""

    def __init__(self, provider: Any, org: str, user: str | None = None) -> None:  # noqa: ANN401
        self._p, self.org, self._user = provider, org, user

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        attr = getattr(self._p, name)
        if not callable(attr):
            return attr

        async def call(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            with bind(OrgContext(self.org, self._user, "job", "job:test", "t")):
                return await attr(*a, **k)
        return call


class StandInProvider(PostgresGraphProvider):
    pass
