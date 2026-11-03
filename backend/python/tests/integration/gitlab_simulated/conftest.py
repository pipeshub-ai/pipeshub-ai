"""One simulated GitLab per test, synced into each integration graph store.

Needs the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml
(see tests/integration/real_graph.py); without it every test here skips.
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
os.environ.setdefault("SECRET_KEY", "gitlab-sim-test-secret-key-0123456789abcdef")

from tests.integration.gitlab_simulated.glsim.model import Behaviour  # noqa: E402
from tests.integration.gitlab_simulated.helpers import allow_the_simulator  # noqa: E402
from tests.integration.gitlab_simulated.world import World, acme  # noqa: E402


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        if "gitlab_simulated" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.timeout(900))


@pytest.fixture(params=["neo4j", "arango"])
async def world(request, monkeypatch) -> AsyncIterator[World]:
    allow_the_simulator(monkeypatch)
    marker = request.node.get_closest_marker("behaviour")
    behaviour = Behaviour(**marker.kwargs) if marker else Behaviour()
    personal = request.node.get_closest_marker("personal") is not None
    # Nothing is wiped: each test's people live on a domain of their own.
    domain = f"acme-{uuid.uuid4().hex[:8]}.example"
    w = await World.create(request.param, monkeypatch, behaviour=behaviour, personal=personal, domain=domain)
    yield w
    gaps = list(dict.fromkeys(w.gl.gaps))
    await w.destroy()
    assert not gaps, "the simulator does not model something the connector used:\n  " + "\n  ".join(gaps)


@pytest.fixture
async def org(world) -> dict[str, Any]:
    """The standard instance with matching PipesHub users, configured on its one project."""
    people = acme(world.gl, world.domain)
    for name in ("maya", "dev", "rita", "pat", "gus", "olga"):
        await world.add_pipeshub_user(people[name].email)
    await world.add_pipeshub_user(world.email("carol"))  # a PipesHub user with no GitLab account
    world.configure(people["project"])
    return people


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "behaviour(**fields): simulator behaviour switches for this test")
    config.addinivalue_line("markers", "personal: run the GitLab Personal connector")
