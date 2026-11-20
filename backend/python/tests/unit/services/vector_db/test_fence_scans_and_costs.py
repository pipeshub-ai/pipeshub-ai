"""What keeps the fence whole: no unfenced pin, no unbracketed drop, and the cost of the checks."""

import ast
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.vector_db.collection_epoch import CollectionEpochs
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from tests.unit.services.vector_db.test_fence_races import _World
from tests.unit.services.vector_db.test_precision_derivation_pgvector import (
    ORG as PG_ORG,
)
from tests.unit.services.vector_db.test_precision_derivation_pgvector import (
    _Runtime,
    _service,
)

log = logging.getLogger("fence-scans")
PYTHON = Path(__file__).resolve().parents[4]
REPO = PYTHON.parent.parent
PROVIDERS = ("app/services/vector_db/qdrant", "app/services/vector_db/pgvector", "app/services/vector_db/opensearch", "app/services/vector_db/redis")
KNOWN_DROP_SITES = {
    ("app/services/vector_db/collection_registry.py", "_drop"),
    ("app/modules/transformers/entity_vectorstore.py", "_drop_collection"),
}


# -- no pin is created outside the fenced claim -------------------------------------------------------------------

def test_no_module_but_the_guard_and_the_pin_store_creates_a_pin() -> None:
    offenders = []
    for path in (PYTHON / "app").rglob("*.py"):
        rel = path.relative_to(PYTHON).as_posix()
        if rel in ("app/services/vector_db/precision_guard.py", "app/services/vector_db/collection_pin.py"):
            continue
        text = path.read_text()
        if "_try_create(" in text or "_claim_unfenced(" in text or ".claim(" in text and "pins" in text and "lease" not in text:
            offenders.append(rel)
    assert offenders == []


def test_the_guard_creates_a_pin_in_one_function_only() -> None:
    tree = ast.parse((PYTHON / "app/services/vector_db/precision_guard.py").read_text())
    makers = {
        fn.name for fn in ast.walk(tree) if isinstance(fn, ast.AsyncFunctionDef)
        for call in ast.walk(fn) if isinstance(call, ast.Call) and getattr(call.func, "attr", "") in ("_try_create", "_claim_unfenced")
    }
    assert makers == {"_claim"}


# -- every drop of a collection is bracketed by a reset -----------------------------------------------------------

def _drop_problems(source: str) -> list[tuple[str, str]]:
    """(function, problem) for each function that drops vectors without ``begin_reset`` before and ``confirm_reset`` after."""
    problems: list[tuple[str, str]] = []
    for fn in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        drops, begins, confirms = [], [], []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = ast.unparse(node.func.value)
                if node.func.attr == "delete_collection" and "vector_db" in receiver:
                    drops.append(node.lineno)
                elif node.func.attr == "begin_reset":
                    begins.append(node.lineno)
                elif node.func.attr == "confirm_reset":
                    confirms.append(node.lineno)
            if (
                isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr"
                and any(isinstance(a, ast.Constant) and a.value == "delete_collection" for a in node.args)
            ):
                problems.append((fn.name, "an indirect drop"))
        for line in drops:
            if not any(b < line for b in begins):
                problems.append((fn.name, "no begin_reset before the drop"))
            elif not any(c > line for c in confirms):
                problems.append((fn.name, "no confirm_reset after the drop"))
    return problems


def _drop_sites() -> set[tuple[str, str]]:
    found = set()
    roots = [PYTHON / "app", REPO / "scripts"]
    for root in roots:
        for path in root.rglob("*.py") if root.exists() else []:
            rel = path.relative_to(PYTHON).as_posix() if path.is_relative_to(PYTHON) else path.relative_to(REPO).as_posix()
            if rel.startswith(PROVIDERS):
                continue
            source = path.read_text()
            if "delete_collection" not in source:
                continue
            for fn in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
                if any(
                    isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "delete_collection"
                    and "vector_db" in ast.unparse(c.func.value)
                    for c in ast.walk(fn)
                ):
                    found.add((rel, fn.name))
    return found


def test_every_drop_of_a_vector_collection_is_between_a_new_epoch_and_its_confirmation() -> None:
    assert _drop_sites() == KNOWN_DROP_SITES  # a new drop site must be added here, and bracketed
    for rel, _ in KNOWN_DROP_SITES:
        assert _drop_problems((PYTHON / rel).read_text()) == []


def test_the_scan_refuses_an_unbracketed_drop_and_a_reversed_order() -> None:
    bare = "async def f(self):\n    await self.vector_db_service.delete_collection('c')\n"
    reversed_ = (
        "async def f(self):\n    t = 1\n    await self.vector_db_service.delete_collection('c')\n"
        "    await self.epochs.confirm_reset(t)\n    await self.epochs.begin_reset('c')\n"
    )
    in_order = (
        "async def f(self):\n    t = await self.epochs.begin_reset('c')\n"
        "    await self.vector_db_service.delete_collection('c')\n    await self.epochs.confirm_reset(t)\n"
    )
    indirect = "async def f(self):\n    await getattr(self.svc, 'delete_collection')('c')\n"
    assert _drop_problems(bare) and _drop_problems(reversed_) and _drop_problems(indirect)
    assert _drop_problems(in_order) == []


# -- the cost -----------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_steady_state_batch_reads_the_store_four_times_and_lists_nothing() -> None:
    """Capture (1), the pin check (1) and the check after the write (the pin and the epoch, 2). Before the epochs:
    the pin check alone for a record batch (1), and with the check after the write for an entity batch (2)."""
    world = _World()
    await world.batch(INT8)  # the first batch creates the epoch and the pin
    reads: list[str] = []
    lists: list[str] = []
    store = world.config.store
    real_get, real_list = store.get_key, store.list_keys_in_directory

    async def get_key(key, **kw):  # noqa: ANN202
        reads.append(key)
        return await real_get(key, **kw)

    async def list_keys(directory):  # noqa: ANN202
        lists.append(directory)
        return await real_list(directory)

    store.get_key, store.list_keys_in_directory = get_key, list_keys
    await world.batch(INT8)
    assert len(reads) == 4 and lists == []


@pytest.mark.asyncio
async def test_control_a_capture_that_listed_the_tokens_would_list_on_every_batch(monkeypatch) -> None:
    world = _World()
    await world.batch(INT8)
    real = CollectionEpochs.capture

    async def listing(self, collection):  # noqa: ANN202
        await self.states(collection)
        return await real(self, collection)

    monkeypatch.setattr(CollectionEpochs, "capture", listing)
    lists: list[str] = []
    real_list = world.config.store.list_keys_in_directory

    async def list_keys(directory):  # noqa: ANN202
        lists.append(directory)
        return await real_list(directory)

    world.config.store.list_keys_in_directory = list_keys
    await world.batch(INT8)
    assert lists != []


# -- the shortcut of a store with a space per organisation ----------------------------------------------------------

def _pg_guard(clock):  # noqa: ANN202
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.in_memory_store import InMemoryKeyValueStore

    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    runtime = _Runtime()
    pins = CollectionPinStore(config, log, clock=clock)
    guard = PrecisionGuard(pins, _service(runtime), log)
    guard._db.delete_points = AsyncMock()  # the sweep's delete is not what these tests are about
    return guard, pins, runtime


def _scans(runtime: _Runtime) -> int:
    return sum("count_filtered" in c["sql"] for c in runtime.calls)


@pytest.mark.asyncio
async def test_an_fp32_claim_skips_the_scan_while_the_index_is_quiet_and_scans_inside_the_window_after_a_cleanup(monkeypatch) -> None:
    now = [1_000_000.0]
    monkeypatch.setattr("app.services.vector_db.precision_guard.sweep_window_seconds", lambda: 5700.0)
    guard, pins, runtime = _pg_guard(lambda: now[0])
    await guard.verify_batch("records", PG_ORG, FP32, fallback=False)
    assert _scans(runtime) == 0  # quiet: nothing was ever released or reset
    # a cleanup: the space is recreated, its pin is released
    await pins.epochs.confirm_reset(await pins.epochs.begin_reset("records"))
    await pins.release_collection("records")
    runtime.calls.clear()
    await guard.verify_batch("records", PG_ORG, FP32, fallback=False)
    assert _scans(runtime) > 0  # inside the window the claim counts the points first
    # the window passes, the token is swept, and the pin is released again by a replacement
    now[0] += 6000
    await guard.sweep_dead("records", PG_ORG)
    await pins.release_collection("records")
    await pins.epochs._config.delete_config(pins.epochs._pin_key("records", PG_ORG))
    runtime.calls.clear()
    await guard.verify_batch("records", PG_ORG, FP32, fallback=False)
    assert _scans(runtime) == 0


@pytest.mark.asyncio
async def test_a_pin_released_in_a_space_makes_that_space_scan_and_not_the_others() -> None:
    guard, pins, runtime = _pg_guard(lambda: 1_000_000.0)
    await guard.verify_batch("records", PG_ORG, FP32, fallback=False)
    await pins.release("records", PG_ORG)
    runtime.calls.clear()
    await guard.verify_batch("records", PG_ORG, FP32, fallback=False)
    assert _scans(runtime) > 0
    other = "b" * 24
    runtime.calls.clear()
    await guard.verify_batch("records", other, FP32, fallback=False)
    assert _scans(runtime) == 0
