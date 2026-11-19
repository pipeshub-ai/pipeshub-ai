"""The record consumers with nothing extra supplied behave exactly as they did before the planner hook existed.

The goldens in ``goldens/record_consumer_seam_off`` were written by ``tests/support/regen_record_consumer_goldens.py``
on the commit before the hook. Each test replays the same generated stream through the real read, dispatch and
claim code on the in-memory brokers of ``tests/support/record_consumer_world.py``, with an injected clock, and
compares dispatch order, the commit or ack sequence, the buffer, remembered-position and parked-entry counters
step by step, and the claim statements of the queue.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tests.support import record_consumer_world as world

GOLDENS = Path(__file__).parent / "goldens" / "record_consumer_seam_off"

pytestmark = pytest.mark.timeout(240)


@pytest.fixture(autouse=True)
def _world_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in world.GOLDEN_ENV.items():
        monkeypatch.setenv(key, value)
    logging.disable(logging.CRITICAL)


def golden(kind: str) -> dict:
    return json.loads((GOLDENS / f"{kind}.json").read_text())


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
def test_constructor_state_is_the_default(kind: str) -> None:
    assert world.constructor_state(kind) == golden(kind)["constructor"]


@pytest.mark.parametrize("kind", ["kafka", "redis", "pg"])
async def test_replay_equals_the_golden(kind: str) -> None:
    assert await world.collect(kind) == golden(kind)["run"]


def test_the_queue_module_is_unchanged() -> None:
    import hashlib

    source = Path(world.__file__).parents[2] / "app" / "services" / "messaging" / "postgres" / "queue.py"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == golden("pg")["queue_sha256"]
