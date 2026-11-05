"""The validator on the per-record path: it must not rebuild its compiled schema per call, and it must be safe to share between threads.

These are guards, not benchmarks. The time budget is deliberately generous: it exists to catch a regression that re-compiles the schema on every
call (the failure mode ``node_validator`` documents: ~60% of a sync's CPU went into jsonschema setup), which is one to two orders of magnitude
slower than the budget allows. It does not measure how fast validation is, and a slow CI host must not turn it red.

Status against the graph-schema-validation change: these exercise ``validate_node`` and ``validate_node_update``, which exist today, so they run
green now and must stay green once ``validate_node_upsert``, the edge validators and the strict ``additionalProperties`` rule land.
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import jsonschema
import pytest

from app.schema import node_validator
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError

RECORD_TYPES = ["FILE", "WEBPAGE", "MESSAGE", "MAIL", "TICKET", "LINK"]
INDEXING_STATUSES = ["NOT_STARTED", "IN_PROGRESS", "COMPLETED", "FAILED"]


def make_record(rng: random.Random, i: int) -> dict[str, Any]:
    """A record document the schema accepts; optional fields vary, and some nullable ones are null."""
    doc: dict[str, Any] = {
        "id": f"rec-{i}",
        "orgId": "org-1",
        "recordName": f"Document {i}",
        "externalRecordId": f"ext-{i}",
        "recordType": rng.choice(RECORD_TYPES),
        "origin": "CONNECTOR",
        "connectorId": "conn-1",
        "createdAtTimestamp": 1_700_000_000_000 + i,
        "updatedAtTimestamp": 1_700_000_000_000 + i + rng.randrange(1000),
        "version": rng.randrange(5),
        "isDeleted": False,
        "indexingStatus": rng.choice(INDEXING_STATUSES),
        "mimeType": rng.choice(["text/plain", "application/pdf", None]),
        "externalParentId": rng.choice([f"parent-{rng.randrange(50)}", None]),
        "sizeInBytes": rng.choice([rng.randrange(10**6), None]),
    }
    if rng.random() < 0.3:
        doc["recordProps"] = {"labels": ["a", "b"], "nested": {"depth": rng.randrange(3)}}
    if rng.random() < 0.2:
        doc["p_color"] = rng.choice(["red", "blue"])
    return doc


def make_invalid_record(rng: random.Random, i: int) -> dict[str, Any]:
    doc = make_record(rng, i)
    kind = i % 3
    if kind == 0:
        doc["recordType"] = "NOT_A_RECORD_TYPE"
    elif kind == 1:
        doc.pop("recordName")
    else:
        doc["version"] = "three"
    return doc


def outcome(call: Callable[[], None]) -> str | None:
    try:
        call()
    except SchemaValidationError as e:
        return str(e)
    return None


@contextmanager
def count_validator_builds() -> Iterator[list[int]]:
    """Counts ``check_schema`` runs: the meta-schema walk every compile does, which a per-call re-compile would repeat.

    Validator instances and ``validator_for`` lookups are not counted: jsonschema makes both for every sub-schema it descends into, even on a cache hit."""
    builds = [0]

    def counting_check_schema(real: Callable) -> classmethod:
        def check_schema(cls: Any, *a: Any, **k: Any) -> Any:  # noqa: ANN401
            builds[0] += 1
            return real(*a, **k)
        return classmethod(check_schema)

    classes = [getattr(jsonschema, n) for n in dir(jsonschema) if n.startswith("Draft") and n.endswith("Validator")]
    patches = [patch.object(c, "check_schema", counting_check_schema(c.check_schema)) for c in classes]
    for p in patches:
        p.start()
    try:
        yield builds
    finally:
        for p in reversed(patches):
            p.stop()


def clear_validator_caches() -> None:
    # The cache names are internal; if they are renamed this only makes the cold-start test warm, it does not break it.
    for name in ("_FULL_CACHE", "_PARTIAL_CACHE"):
        getattr(node_validator, name, {}).clear()


def test_10k_records_validate_within_a_generous_budget_that_only_a_per_call_recompile_would_break() -> None:
    rng = random.Random(20260511)
    docs = [make_record(rng, i) for i in range(10_000)]
    validator = NodeSchemaValidator()
    validator.validate_node("records", docs[0])  # compile once, outside the measurement

    started = time.perf_counter()
    for doc in docs:
        validator.validate_node("records", doc)
    elapsed = time.perf_counter() - started

    assert elapsed < 30.0, f"10,000 validations took {elapsed:.2f}s: the schema is probably being re-compiled per call"


def test_every_generated_record_is_valid_and_each_invalid_variant_is_rejected() -> None:
    # Guards the perf data itself: a generator drifting into invalid documents would make the budget test measure the error path.
    rng = random.Random(1)
    validator = NodeSchemaValidator()
    for i in range(300):
        validator.validate_node("records", make_record(rng, i))
        with pytest.raises(SchemaValidationError):
            validator.validate_node("records", make_invalid_record(rng, i))


def test_the_compiled_validator_is_built_once_and_reused_across_calls() -> None:
    rng = random.Random(2)
    validator = NodeSchemaValidator()
    validator.validate_node("records", make_record(rng, 0))
    validator.validate_node_update("records", {"indexingStatus": "COMPLETED"})

    with count_validator_builds() as builds:
        for i in range(500):
            validator.validate_node("records", make_record(rng, i))
            validator.validate_node_update("records", {"indexingStatus": "COMPLETED"})
        for i in range(30):  # a failing document does not rebuild it either
            outcome(lambda i=i: validator.validate_node("records", make_invalid_record(rng, i)))

    assert builds[0] == 0, f"{builds[0]} validator objects were built while validating warm documents"


def test_a_second_validator_object_shares_the_compiled_schema() -> None:
    # The providers each hold their own NodeSchemaValidator; the compiled schema must not be per instance.
    rng = random.Random(3)
    NodeSchemaValidator().validate_node("records", make_record(rng, 0))
    with count_validator_builds() as builds:
        NodeSchemaValidator().validate_node("records", make_record(rng, 1))
    assert builds[0] == 0


def test_eight_threads_get_identical_results_and_no_exception_from_a_cold_cache() -> None:
    rng = random.Random(4)
    docs = [make_record(rng, i) if i % 4 else make_invalid_record(rng, i) for i in range(400)]
    updates = [{"indexingStatus": s} for s in (*INDEXING_STATUSES, "BOGUS")] * 20
    validator = NodeSchemaValidator()

    def run_all() -> list[str | None]:
        return ([outcome(lambda d=d: validator.validate_node("records", d)) for d in docs]
                + [outcome(lambda u=u: validator.validate_node_update("records", u)) for u in updates])

    clear_validator_caches()
    expected = run_all()  # single-threaded baseline
    assert any(r is not None for r in expected) and any(r is None for r in expected)

    clear_validator_caches()  # cold again, so the threads race on the first compile
    start = threading.Barrier(8)

    def worker() -> list[str | None]:
        start.wait()
        return run_all()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [f.result(timeout=20) for f in [pool.submit(worker) for _ in range(8)]]  # .result() re-raises any worker exception

    assert all(r == expected for r in results)


def test_eight_threads_on_a_warm_cache_never_rebuild_the_validator() -> None:
    rng = random.Random(5)
    docs = [make_record(rng, i) for i in range(200)]
    validator = NodeSchemaValidator()
    validator.validate_node("records", docs[0])
    start = threading.Barrier(8)

    def worker() -> None:
        start.wait()
        for d in docs:
            validator.validate_node("records", d)

    with count_validator_builds() as builds, ThreadPoolExecutor(max_workers=8) as pool:
        for f in [pool.submit(worker) for _ in range(8)]:
            f.result(timeout=20)
    assert builds[0] == 0


def best_of(runs: int, fn: Callable[[], None]) -> float:
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        times.append(time.perf_counter() - started)
    return min(times)


def test_a_three_field_update_is_not_slower_than_a_small_multiple_of_a_full_validation() -> None:
    rng = random.Random(6)
    doc = make_record(rng, 0)
    update = {"indexingStatus": "COMPLETED", "lastIndexTimestamp": 1_700_000_001_000, "isDirty": False}
    validator = NodeSchemaValidator()
    validator.validate_node("records", doc)
    validator.validate_node_update("records", update)
    n = 2000

    t_full = best_of(5, lambda: [validator.validate_node("records", doc) for _ in range(n)])
    t_update = best_of(5, lambda: [validator.validate_node_update("records", update) for _ in range(n)])

    # 3x plus a fixed allowance: both are microseconds per call, so a bare ratio would flake on a noisy host.
    assert t_update <= 3 * t_full + 0.05, f"update {t_update:.4f}s vs full {t_full:.4f}s for {n} calls"
