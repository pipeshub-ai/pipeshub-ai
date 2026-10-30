# pyright: ignore-file

"""Dropbox (team and personal) integration fixtures against the Dropbox simulator (DESIGN.md here).

``dropbox_team`` and ``dropbox_personal`` are class-scoped: each loads a simulated Dropbox, makes sure every Dropbox
member (and every outside collaborator) has a PipesHub account with the same email (connector access is keyed by
email), creates the connector with the simulator's OAuth app, exchanges the simulator's refresh token at the
simulator's token endpoint (never Dropbox's), runs the first sync, and on teardown deletes the connector and checks
its graph is gone.

A test class picks its Dropbox and its indexing with class attributes:

- ``TENANT``: a function of the email domain (team) or of the two account emails (personal) returning the
  simulator scenario. Defaults: ``team_scenario`` and ``personal_scenario``.
- ``AUTO_INDEX``: ``True`` to index what syncs (extraction and embeddings); default ``False``, sync only.
- ``SYNC_FILTERS``: the sync filter values the connector is created with; default none.
- ``BEHAVIOURS``: simulator switches for the class (``/_sim/dropbox/behaviours``), e.g. the team member root layout.

The scenario matrix module has its own module-scoped ``scenario_adapter`` fixture, built from ``dropbox_connector``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Callable

import pytest
import pytest_asyncio

from connectors.dropbox_sim.dropbox_sim import DOMAIN, personal_scenario, team_scenario
from connectors.dropbox_sim.dropbox_it import dropbox_connector, pipeshub_people, simulator
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


def _class_setting(request: pytest.FixtureRequest, name: str, default: Any) -> Any:
    value = getattr(request.cls, name, default) if request.cls is not None else default
    return value.__func__ if isinstance(value, staticmethod) else value


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def dropbox_team(
    request: pytest.FixtureRequest,
    pipeshub_client: PipeshubClient,
    users_client: Any,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    sim = simulator()
    tenant: Callable[[str], dict[str, Any]] = _class_setting(request, "TENANT", team_scenario)
    scenario = tenant(DOMAIN)
    sim.load(scenario, _class_setting(request, "BEHAVIOURS", None))
    people = {m["key"]: m["email"] for m in scenario["members"]}
    people.update({e["key"]: e["email"] for e in scenario.get("externals", [])})
    users = await pipeshub_people(users_client, graph_provider, people)
    async with dropbox_connector(pipeshub_client, graph_provider, sim, "Dropbox", "team",
                                 auto_index=_class_setting(request, "AUTO_INDEX", False),
                                 sync_values=_class_setting(request, "SYNC_FILTERS", None)) as connector_id:
        yield {"sim": sim, "connector_id": connector_id, "users": users}


@pytest_asyncio.fixture(scope="class", loop_scope="session")
async def dropbox_personal(
    request: pytest.FixtureRequest,
    pipeshub_client: PipeshubClient,
    users_client: Any,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """The admin connects a Dropbox account registered under a colleague's email: only the admin, who made the
    connector, may find its files (a personal connector is its creator's)."""
    sim = simulator()
    colleague = f"dbx-it-colleague@{DOMAIN}"
    users = await pipeshub_people(users_client, graph_provider, {"colleague": colleague})
    tenant: Callable[[str, str], dict[str, Any]] = _class_setting(request, "TENANT", personal_scenario)
    sim.load(tenant(colleague, f"dbx-it-other@{DOMAIN}"))
    async with dropbox_connector(pipeshub_client, graph_provider, sim, "Dropbox Personal", "personal",
                                 auto_index=_class_setting(request, "AUTO_INDEX", False),
                                 sync_values=_class_setting(request, "SYNC_FILTERS", None)) as connector_id:
        yield {"sim": sim, "connector_id": connector_id, "colleague": users["colleague"]}
