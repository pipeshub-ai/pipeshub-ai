"""Helpers for the graph tests: a provider bound to one org."""
from __future__ import annotations

import secrets
from typing import Any

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from tests.permissions.scenario import Scenario, load_all


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


def corpus() -> list[Scenario]:
    """The scenario corpus PostgreSQL is held to: every scenario but the connector access-limit ones (tag ``access-limit``),
    whose rule PostgreSQL's gate does not have yet. Step 3 of the licence gate adds it there and drops this filter."""
    return [s for s in load_all() if "access-limit" not in s.tags]
