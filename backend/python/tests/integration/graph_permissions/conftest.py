"""Fixtures for the knowledge-hub permission traversal parity harness.

The harness proves one thing: the new traversal returns *identical* results on
Neo4j and ArangoDB for the agreed rules.

Start the throwaway stores, then run:

    docker compose -p kh-perm -f backend/python/tests/integration/graph_permissions/docker-compose.yml up -d
    pytest tests/integration/graph_permissions -m integration --timeout=300

Environment variables are deliberately harness-scoped. The production names
(``NEO4J_URI``, ``ARANGO_URL``, ...) are **not** read: a developer shell
routinely exports them pointing at a real stack, and this suite wipes whatever
it connects to. Override these instead, defaulted to the compose file above:

    KH_PERM_NEO4J_URI        bolt://localhost:7688
    KH_PERM_NEO4J_USERNAME   neo4j
    KH_PERM_NEO4J_PASSWORD   khpermpass
    KH_PERM_NEO4J_DATABASE   neo4j
    KH_PERM_ARANGO_URL       http://localhost:8530
    KH_PERM_ARANGO_USERNAME  root
    KH_PERM_ARANGO_PASSWORD  khpermpass
    KH_PERM_ARANGO_DB        kh_perm_test

``Neo4jProvider.connect()`` reads ``NEO4J_*`` from the process environment, so
the fixture overwrites those from the values above — inside the test process
only, never the caller's shell.

Both stores are wiped on setup rather than teardown, so a run always starts
clean even if a previous run crashed.

**This suite is destructive.** It refuses to run against anything but loopback,
and refuses the conventional ports (7687/7474, 8529) outright, because a
development instance usually lives there and a wipe would take real data with
it. Override only if you know the target is disposable:

    KH_PERM_ALLOW_DEFAULT_PORTS=1
"""

import logging
import os
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.integration

def pytest_collection_modifyitems(items) -> None:
    """Give these tests the timeout their containers need.

    pytest.ini sets ``--timeout=30``, which suits a unit test and is far too
    short for one that talks to two databases. Without this, running the suite
    without the documented ``--timeout=300`` override reports timeouts rather
    than real failures, which reads as a broken harness rather than a bug.
    """
    for item in items:
        if "graph_permissions" in str(item.path) and not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(300))


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}

# Ports a developer's own Neo4j/Arango normally occupies. Wiping one of those
# would destroy real data, so they are refused unless explicitly allowed.
_RESERVED_DEV_PORTS = {7474, 7687, 8529}


def _assert_disposable(url: str, label: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in _LOOPBACK:
        raise RuntimeError(
            f"{label}={url!r} is not loopback. This suite wipes its target; "
            f"refusing to touch a remote host."
        )
    port = parsed.port
    if port in _RESERVED_DEV_PORTS and os.environ.get("KH_PERM_ALLOW_DEFAULT_PORTS") != "1":
        raise RuntimeError(
            f"{label}={url!r} uses port {port}, where a development instance "
            f"normally runs, and this suite wipes its target. Start the "
            f"throwaway stores from graph_permissions/docker-compose.yml, or "
            f"set KH_PERM_ALLOW_DEFAULT_PORTS=1 if the target is disposable."
        )


def _logger() -> logging.Logger:
    return logging.getLogger("kh_perm_harness")


class _StubConfigService:
    """Minimal ConfigurationService: ArangoHTTPProvider.connect() asks it for
    ``/services/arangodb`` and wants ``url``/``username``/``password``/``db``.
    Neo4jProvider reads its own credentials straight from the environment."""

    def __init__(self, arango: dict) -> None:
        self._arango = arango

    async def get_config(self, key: str, **_kwargs) -> dict | None:
        from app.config.constants.service import config_node_constants
        from app.services.graph_db.interface.graph_db_provider import KH_LISTING_STATE_FLAG

        from .loaders import LISTING_STATE

        if key == config_node_constants.ARANGODB.value:
            return dict(self._arango)
        if key == KH_LISTING_STATE_FLAG and LISTING_STATE:
            return {"done": True}
        return None


def _neo4j_settings() -> dict:
    return {
        "uri": os.environ.get("KH_PERM_NEO4J_URI", "bolt://localhost:7688"),
        "username": os.environ.get("KH_PERM_NEO4J_USERNAME", "neo4j"),
        "password": os.environ.get("KH_PERM_NEO4J_PASSWORD", "khpermpass"),
        "database": os.environ.get("KH_PERM_NEO4J_DATABASE", "neo4j"),
    }


def _arango_settings() -> dict:
    return {
        "url": os.environ.get("KH_PERM_ARANGO_URL", "http://localhost:8530"),
        "username": os.environ.get("KH_PERM_ARANGO_USERNAME", "root"),
        "password": os.environ.get("KH_PERM_ARANGO_PASSWORD", "khpermpass"),
        "db": os.environ.get("KH_PERM_ARANGO_DB", "kh_perm_test"),
    }


@pytest.fixture(scope="module")
async def loaded_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings):
    """The acceptance graph, loaded into both backends.

    Depending on the provider fixtures is what orders this correctly: they wipe
    at setup, so a module that loaded the graph and then handed over to another
    module would have its data cleared underneath it. Every module that queries
    the graph therefore loads its own copy, and no module may rely on another
    having loaded it.
    """
    from .fixture_graph import build_fixture
    from .loaders import load_into_arango, load_into_neo4j

    nodes, edges = build_fixture()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, nodes, edges)
    return {"nodes": nodes, "edges": edges}


@pytest.fixture(scope="module")
def neo4j_settings() -> dict:
    return _neo4j_settings()


@pytest.fixture(scope="module")
def arango_settings() -> dict:
    return _arango_settings()


async def _wipe_neo4j(settings: dict) -> None:
    """Delete every node and relationship in the target database.

    Neo4j Community has only the one user database, so an isolated database
    per run is not available — the harness owns the whole container instead.
    """
    from neo4j import AsyncGraphDatabase

    _assert_disposable(settings["uri"], "NEO4J_URI")
    driver = AsyncGraphDatabase.driver(
        settings["uri"], auth=(settings["username"], settings["password"])
    )
    try:
        await driver.verify_connectivity()
        async with driver.session(database=settings["database"]) as session:
            await session.run("MATCH (n) DETACH DELETE n")
    finally:
        await driver.close()


async def _drop_arango_db(settings: dict) -> None:
    """Drop the test database so provider.connect() recreates it empty."""
    import aiohttp

    _assert_disposable(settings["url"], "ARANGO_URL")
    # The URL check is not enough on its own: this drops whatever database the
    # env var names, so KH_PERM_ARANGO_DB=pipeshub against a local Arango on a
    # non-default port would destroy it.
    if not settings["db"].startswith("kh_perm"):
        raise RuntimeError(
            f"KH_PERM_ARANGO_DB={settings['db']!r} is outside the kh_perm* "
            f"namespace, and this suite drops the database it names."
        )
    auth = aiohttp.BasicAuth(settings["username"], settings["password"])
    url = f"{settings['url'].rstrip('/')}/_db/_system/_api/database/{settings['db']}"
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.delete(url) as resp:
            # 404 simply means this is the first run.
            if resp.status not in (200, 404):
                raise RuntimeError(
                    f"Could not drop Arango database {settings['db']}: "
                    f"{resp.status} {await resp.text()}"
                )


@pytest.fixture(scope="module")
async def neo4j_provider():
    """A connected Neo4jProvider against an empty database, or skip."""
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    settings = _neo4j_settings()
    try:
        await _wipe_neo4j(settings)
    except RuntimeError:
        raise  # a misconfigured target is a failure, never a silent skip
    except Exception as exc:
        pytest.skip(f"Neo4j not available at {settings['uri']} — {exc}")

    # Restored on teardown: left set, these would silently point any later test
    # that builds a Neo4jProvider from the environment at the throwaway
    # container — including one that then wipes it.
    previous_env = {
        key: os.environ.get(key)
        for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    }
    os.environ["NEO4J_URI"] = settings["uri"]
    os.environ["NEO4J_USERNAME"] = settings["username"]
    os.environ["NEO4J_PASSWORD"] = settings["password"]
    os.environ["NEO4J_DATABASE"] = settings["database"]

    try:
        provider = Neo4jProvider(logger=_logger(), config_service=_StubConfigService({}))
        if not await provider.connect():
            pytest.skip(f"Neo4jProvider could not connect to {settings['uri']}")

        # As on Arango, connect() establishes the session but builds no schema:
        # constraints and indexes come from ensure_schema(), which production calls
        # at startup. Its property-existence constraints are Enterprise-only and
        # fail individually on the Community image; each query is tried separately,
        # so the uniqueness constraints and indexes still land.
        if not await provider.ensure_schema():
            pytest.fail(f"ensure_schema() failed for Neo4j at {settings['uri']}")

        yield provider
    finally:
        # In the finally, not beside the yield: a test that errors would
        # otherwise skip the disconnect and leak the driver.
        await provider.disconnect()
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
async def arango_provider():
    """A connected ArangoHTTPProvider against an empty database, or skip."""
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    settings = _arango_settings()
    try:
        await _drop_arango_db(settings)
    except RuntimeError:
        raise
    except Exception as exc:
        pytest.skip(f"ArangoDB not available at {settings['url']} — {exc}")

    provider = ArangoHTTPProvider(_logger(), _StubConfigService(settings))
    if not await provider.connect():
        pytest.skip(f"ArangoHTTPProvider could not connect to {settings['url']}")

    # connect() only ensures the database exists — its collection-creation loop
    # is commented out. Collections, the named graph and its edge definitions
    # come from ensure_schema(), which production calls from the connector
    # service at startup.
    if not await provider.ensure_schema():
        pytest.fail(f"ensure_schema() failed for Arango database {settings['db']!r}")

    yield provider
    await provider.disconnect()

