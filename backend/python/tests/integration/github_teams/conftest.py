"""Fixtures: one World per test, on each graph database (NEO4J_IT_URI / ARANGO_IT_URL).

A module that sets ``BOTH_TRASH_MODES = True`` runs every test with the trash (soft
delete) off and on.
"""

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.integration.github_teams.harness import World, acme
from tests.integration.github_teams.sim.model import Behaviour

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
os.environ.setdefault("SECRET_KEY", "github-teams-it-secret-key-0123456789abcdef")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "behaviour(**fields): simulator behaviour switches for this test")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "backend" in metafunc.fixturenames:
        metafunc.parametrize("backend", ["neo4j", "arango"])
    if "trash" in metafunc.fixturenames:
        both = getattr(metafunc.module, "BOTH_TRASH_MODES", False)
        metafunc.parametrize("trash", [False, True] if both else [False],
                             ids=["trash-off", "trash-on"] if both else ["trash-off"])


@pytest.fixture
async def world(
    request: pytest.FixtureRequest, backend: str, trash: bool, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[World]:
    marker = request.node.get_closest_marker("behaviour")
    behaviour = Behaviour(**marker.kwargs) if marker else Behaviour()
    w = await World.create(backend, monkeypatch, behaviour=behaviour, soft_delete=trash)
    try:
        yield w
        gaps = list(dict.fromkeys(w.gh.gaps))
    finally:
        await w.destroy()
    assert not gaps, "the simulator does not model something the connector used:\n  " + "\n  ".join(gaps)


@pytest.fixture
async def org(world: World) -> dict[str, Any]:
    """The standard org with matching PipesHub users, configured on its one repository."""
    people = acme(world.gh)
    for name in ("alice", "bob", "olga"):
        await world.add_pipeshub_user(people[name].email)
    await world.add_pipeshub_user("carol@acme.com")  # a PipesHub user with no GitHub account
    world.configure([people["repo"]])
    return people
