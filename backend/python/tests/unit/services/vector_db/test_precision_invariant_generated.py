"""Generated interleavings: no index ever holds text points of two precisions.

Each sequence mixes writers of both precisions, a server that changes precision between batches with no connection
error, boots that fall back to fp32, two models (two indexes), two processes with their own caches over one
configuration backend, and cleanups that drop an index and are heard of by the cleaning process only. The invariant is checked on the points that were actually
upserted (each carries the tag of the batch that made it), never on pin states.
"""

import logging
import random
from types import SimpleNamespace

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard, PrecisionUnreadable

log = logging.getLogger("generated")
ORG = "o1"
SEEDS = (20261113, 31415926)
SEQUENCES_PER_SEED = 100
STEPS = 60


class _Db:
    """Points per collection; every count the guard asks for is a positive tag count."""

    def __init__(self) -> None:
        self.points: dict[str, list[dict]] = {}

    async def filter_collection(self, must=None, **_k):  # noqa: ANN202
        return dict(must or {})

    def _match(self, p: dict, expression: dict) -> bool:
        return all((p.get(k) in v) if isinstance(v, list) else p.get(k) == v for k, v in expression.items())

    async def count_points(self, collection, expression):  # noqa: ANN202
        return sum(self._match(p, expression) for p in self.points.get(collection, []))

    async def delete_points(self, collection, expression):  # noqa: ANN202
        self.points[collection] = [p for p in self.points.get(collection, []) if not self._match(p, expression)]

    async def get_collection_info(self, collection):  # noqa: ANN202
        return SimpleNamespace(exists=True, points_count=len(self.points.get(collection, [])), dense_dimension=4)

    async def make_searchable(self, collection):  # noqa: ANN202
        return None


class _Server:
    """One embedding server: per model, what it serves now and whether that is a fallback."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.state = {m: (rng.choice([FP32, INT8]), False) for m in ("alpha", "beta")}

    def flip(self, model: str) -> None:
        serving, _ = self.state[model]
        self.state[model] = (INT8 if serving == FP32 else FP32, False)

    def fall_back(self, model: str) -> None:
        self.state[model] = (FP32, True)  # int8 was asked for, fp32 is served

    def embed(self, model: str) -> tuple[str, bool]:
        return self.state[model]


async def _sequence(seed: int) -> None:
    rng = random.Random(seed)
    db, server = _Db(), _Server(rng)
    store = InMemoryKeyValueStore(log)
    clock = [1000.0]
    services = [ConfigurationService(log, store) for _ in range(2)]
    guards = [PrecisionGuard(CollectionPinStore(service, log), db, log) for service in services]
    collections = {"alpha": "records-alpha", "beta": "records-beta"}

    async def write(process: int, model: str, *, flip_before_store: bool) -> None:
        witnessed = server.embed(model)  # what the response said about this batch
        if flip_before_store:
            server.flip(model)  # the server changes after the vectors were made, before the next batch
        precision, fallback = witnessed
        collection = collections[model]
        try:
            await guards[process].verify_batch(collection, ORG, precision, fallback=fallback, intent=INT8)
        except (PrecisionFlip, PrecisionUnreadable):
            return  # refused before the upsert: handed back
        db.points.setdefault(collection, []).append({"orgId": ORG, "virtualRecordId": "v", "embedPrecision": precision})

    async def cleanup(model: str) -> None:
        """Only the process that runs the cleanup learns of it; the others keep what they cached."""
        collection = collections[model]
        cleaner = rng.randrange(2)
        db.points[collection] = []
        await CollectionPinStore(services[cleaner], log).release_collection(collection)
        services[cleaner].cache.clear()

    for _ in range(STEPS):
        clock[0] += rng.uniform(0, 5)
        action = rng.random()
        model = rng.choice(["alpha", "beta"])
        if action < 0.55:
            await write(rng.randrange(2), model, flip_before_store=rng.random() < 0.15)
        elif action < 0.7:
            server.flip(model)
        elif action < 0.8:
            server.fall_back(model)
        elif action < 0.9:
            await cleanup(model)
        else:  # two processes write the same index back to back, in either order
            first = rng.randrange(2)
            await write(first, model, flip_before_store=False)
            await write(1 - first, model, flip_before_store=rng.random() < 0.3)
        for collection, stored in db.points.items():
            assert len({p["embedPrecision"] for p in stored}) <= 1, f"seed {seed}: {collection} holds both precisions"


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", SEEDS)
async def test_no_index_ever_holds_two_precisions(seed: int) -> None:
    for n in range(SEQUENCES_PER_SEED):
        await _sequence(seed * 1000 + n)


@pytest.mark.asyncio
async def test_the_generator_finds_a_mixed_index_when_the_check_is_removed(monkeypatch) -> None:
    """The generator is not vacuous: with the per-batch check a no-op it reaches a mixed index."""

    async def no_check(self, *a, **k):  # noqa: ANN202
        return None

    monkeypatch.setattr(PrecisionGuard, "verify_batch", no_check)
    with pytest.raises(AssertionError, match="holds both precisions"):
        for n in range(SEQUENCES_PER_SEED):
            await _sequence(SEEDS[0] * 1000 + n)
