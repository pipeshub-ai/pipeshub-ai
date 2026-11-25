"""Every service that writes to the main Redis checks its maxmemory-policy at start-up, and the check refuses allkeys-*."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import replay_set

APP = Path(__file__).resolve().parents[2] / "app"

# The service's start-up runs the check; indexing through its wrapper, which also feeds /health.
CHECKED = {
    "connectors_main.py": "check_main_redis_eviction_policy",
    "query_main.py": "check_main_redis_eviction_policy",
    "parsing_main.py": "check_main_redis_eviction_policy",
    "extraction_main.py": "check_main_redis_eviction_policy",
    "docling_main.py": "check_main_redis_eviction_policy",
    "indexing_main.py": "_check_redis_eviction_policy",
}
# No connection to PipesHub's Redis at all.
NO_REDIS = {"embedding_main.py", "docker_proxy_main.py"}
# The agent harness's processes that write to the main Redis, checked where they start (no FastAPI lifespan): the worker
# (live bus, run hints) and the model gateway (the provider gate's shared counters).
CHECKED_AT = {
    "harness_main.py": ("run_worker", "check_main_redis_eviction_policy"),
    "model_gateway_main.py": ("run_gateway", "check_main_redis_eviction_policy"),
}
# The sandbox plane's components hold Redis users that may read only their own component document (ADR-KEY-02): they
# cannot run INFO, and write nothing to the main Redis.
READS_ONLY = {"sandbox_broker_main.py", "egress_proxy_main.py"}


def _calls_in_lifespan(path: Path, function: str = "lifespan") -> set[str]:
    tree = ast.parse(path.read_text())
    lifespan = next(
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == function
    )
    return {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call)
    }


def test_every_service_entry_point_is_classified() -> None:
    assert {p.name for p in APP.glob("*_main.py")} == set(CHECKED) | NO_REDIS | set(CHECKED_AT) | READS_ONLY


@pytest.mark.parametrize(("main", "where"), sorted(CHECKED_AT.items()))
def test_a_harness_process_runs_the_check_where_it_starts(main: str, where: tuple[str, str]) -> None:
    function, call = where
    assert call in _calls_in_lifespan(APP / main, function)


@pytest.mark.parametrize(("main", "call"), sorted(CHECKED.items()))
def test_the_service_start_up_runs_the_check(main: str, call: str) -> None:
    assert call in _calls_in_lifespan(APP / main)


def _redis(policy: str) -> MagicMock:
    redis = MagicMock()
    redis.info = AsyncMock(return_value={"maxmemory_policy": policy})
    redis.aclose = AsyncMock()
    return redis


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REDIS_MODE", "KV_STORE_TYPE", "MESSAGE_BROKER"):
        monkeypatch.delenv(name, raising=False)
    replay_set._eviction_warning = None
    yield
    replay_set._eviction_warning = None


async def test_allkeys_stops_the_start_and_the_client_is_closed() -> None:
    redis = _redis("allkeys-lru")
    with patch("app.services.vector_db.rebuild_state.redis_from_config_service", AsyncMock(return_value=redis)), \
            pytest.raises(replay_set.UnsafeEvictionPolicyError):
        await replay_set.check_main_redis_eviction_policy(MagicMock(), MagicMock(), "connectors")
    redis.aclose.assert_awaited_once()


async def test_noeviction_starts() -> None:
    with patch("app.services.vector_db.rebuild_state.redis_from_config_service",
               AsyncMock(return_value=_redis("noeviction"))):
        await replay_set.check_main_redis_eviction_policy(MagicMock(), MagicMock(), "query")
    assert replay_set.eviction_policy_warning() is None


async def test_an_unreachable_redis_does_not_stop_the_start() -> None:
    logger = MagicMock()
    with patch("app.services.vector_db.rebuild_state.redis_from_config_service", AsyncMock(side_effect=OSError("down"))):
        await replay_set.check_main_redis_eviction_policy(MagicMock(), logger, "parsing")
    logger.warning.assert_called_once()


@pytest.mark.parametrize("mode", ["postgres", "inprocess"])
async def test_no_redis_server_is_not_checked(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("REDIS_MODE", mode)
    factory = AsyncMock()
    with patch("app.services.vector_db.rebuild_state.redis_from_config_service", factory):
        await replay_set.check_main_redis_eviction_policy(MagicMock(), MagicMock(), "connectors")
    factory.assert_not_awaited()
