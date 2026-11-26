"""QA-12 (CG-12, TSC-G5, CST-G4): the credential migration interrupted at every write.

The process dies at the n-th write the migration makes (a KV ``set_config``/``delete_config`` or a binder call), for
every n the run makes. At the crash no binding exists that the ledger does not name (``revert`` could not find it)
and nothing held back has a binding (no widening). A rerun converges to the uninterrupted run's state: the same
bindings with the same material, the same ledger, the old paths byte-equal. ``revert`` interrupted the same way, or
run straight after an interrupted ``apply``, converges to the state before the migration. A store outage surfaces
as a typed error naming no secret.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.credentials.migration import LEDGER_PATH, LedgerUnwritable
from harness_core.credentials.errors import CredentialStoreUnavailable
from harness_core.security.token_patterns import find_forbidden
from tests.unit.harness.credentials.test_binding_migration import (
    MCP_TOKEN,
    ORG,
    REFRESH,
    SLACK,
    old_paths,
    world,
)

if TYPE_CHECKING:
    from app.harness.credentials.migration import CredentialBindingMigration
    from tests.unit.harness.credentials.binding_support import FakeBindingWriter
    from tests.unit.harness.credentials.support import DictConfig

HELD = {("ts-2", "user:u1"), ("ts-1", "user:ghost"), ("ts-1", "user:u2")}


class Died(Exception):
    """The process stopped here."""


class Failpoint:
    """Counts the migration's writes and dies at the ``at``-th; ``at=0`` never dies."""

    def __init__(self, config: DictConfig, writer: FakeBindingWriter) -> None:
        self.at = 0
        self.writes = 0
        for target, names in ((config, ("set_config", "delete_config")), (writer, ("import_record", "revert_import"))):
            for name in names:
                setattr(target, name, self._wrap(getattr(target, name)))

    def _wrap(self, call: Any) -> Any:  # noqa: ANN401
        async def wrapped(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            self.writes += 1
            if self.writes == self.at:
                raise Died
            return await call(*args, **kwargs)

        return wrapped

    def arm(self, at: int) -> None:
        self.at, self.writes = at, 0


def state(config: DictConfig, writer: FakeBindingWriter) -> dict[str, Any]:
    """What a reader sees: each live binding's owner and current material, the ledger without times, the old paths."""
    bindings = {}
    for row in writer.rows.values():
        document = config.values[writer.vault(row["orgId"]).path(row["id"])]
        bindings[(row["orgId"], row["installation"], row["owner"])] = (row["status"], document["lastVersion"])
    ledger = sorted(
        tuple(sorted((k, v) for k, v in e.items() if k != "at")) for e in (config.values.get(LEDGER_PATH) or {}).get("imported", [])
    )
    return {"bindings": bindings, "ledger": ledger, "old": old_paths(config)}


def assert_no_orphan_and_no_widening(config: DictConfig, writer: FakeBindingWriter) -> None:
    named = {(e["orgId"], e["installation"], e["owner"]) for e in (config.values.get(LEDGER_PATH) or {}).get("imported", [])}
    for row in writer.rows.values():
        assert (row["orgId"], row["installation"], row["owner"]) in named, "a binding no ledger entry names"
        assert (row["installation"], row["owner"]) not in HELD, "a held record was bound"


async def clean_writes(mode: str) -> tuple[int, dict[str, Any]]:
    config, writer, migration = world()
    point = Failpoint(config, writer)
    if mode == "revert":
        _ = await migration.apply()
        point.arm(0)
        _ = await migration.revert()
    else:
        _ = await migration.apply()
    return point.writes, state(config, writer)


def armed() -> tuple[DictConfig, FakeBindingWriter, CredentialBindingMigration, Failpoint]:
    config, writer, migration = world()
    return config, writer, migration, Failpoint(config, writer)


async def test_qa12_1_apply_dies_at_every_write_and_the_rerun_converges() -> None:
    total, expected = await clean_writes("apply")
    assert total >= 6
    for n in range(1, total + 1):
        config, writer, migration, point = armed()
        before = old_paths(config)
        point.arm(n)
        with pytest.raises(Died):
            _ = await migration.apply()
        assert_no_orphan_and_no_widening(config, writer)
        assert old_paths(config) == before, f"apply wrote an old path before dying at write {n}"
        point.arm(0)
        report = await migration.apply()
        assert report.mismatched == [], f"write {n}"
        assert state(config, writer) == expected, f"died at write {n}"


async def test_qa12_2_revert_dies_at_every_write_and_the_rerun_restores_the_start() -> None:
    total, _ = await clean_writes("revert")
    config0, writer0, _ = world()
    start = state(config0, writer0)
    for n in range(1, total + 1):
        config, writer, migration, point = armed()
        _ = await migration.apply()
        point.arm(n)
        with pytest.raises(Died):
            _ = await migration.revert()
        point.arm(0)
        report = await migration.revert()
        assert report.held == [], f"died at write {n}: {report.held}"
        assert state(config, writer) == {**start, "ledger": []}, f"died at write {n}"
        assert not [k for k in config.values if k.startswith("/services/controlPlane/credentials/")]


async def test_qa12_3_revert_straight_after_an_interrupted_apply_restores_the_start() -> None:
    total, _ = await clean_writes("apply")
    config0, writer0, _ = world()
    start = state(config0, writer0)
    for n in range(1, total + 1):
        config, writer, migration, point = armed()
        point.arm(n)
        with pytest.raises(Died):
            _ = await migration.apply()
        point.arm(0)
        report = await migration.revert()
        assert report.held == [], f"died at write {n}: {report.held}"
        assert state(config, writer) == {**start, "ledger": []}, f"died at write {n}"


class _Down:
    """A KV store that stops answering after ``after`` reads, as ``ConfigurationService`` reports it: the driver's
    error with ``raise_on_error``, else the caller's default."""

    def __init__(self, config: DictConfig, after: int) -> None:
        self.reads = 0
        self.after = after
        read = config.get_config

        async def get_config(key: str, default: Any = None, *args: Any, raise_on_error: bool = False, **kwargs: Any) -> Any:  # noqa: ANN401
            self.reads += 1
            if self.reads > self.after:
                if raise_on_error:
                    raise ConnectionError(f"redis: connection refused while reading {key}")
                return default
            return await read(key, default, *args, **kwargs)

        config.get_config = get_config  # type: ignore[method-assign]


async def test_qa12_4_the_store_down_mid_apply_raises_names_no_secret_and_a_rerun_completes() -> None:
    _, expected = await clean_writes("apply")
    after = 0
    while True:
        after += 1
        config, writer, migration = world()
        down = _Down(config, after)
        try:
            report = await migration.apply()
        except (ConnectionError, LedgerUnwritable, CredentialStoreUnavailable) as error:
            text = repr(error) + str(error.__cause__)
            for secret in (SLACK, REFRESH, MCP_TOKEN):
                assert secret not in text
            assert find_forbidden(text) == ()
        else:
            # QA12-F2: an outage is never reported as a scoping verdict on a record the org can see.
            assert {(e["installation"], e["owner"], e["reason"]) for e in report.held} == {
                ("ts-2", "user:u1", "not_visible"),
                ("ts-1", "user:ghost", "owner_unknown"),
            }, f"store down after {after} reads"
        assert_no_orphan_and_no_widening(config, writer)
        if down.reads <= after:
            break
        down.after = 10**9
        _ = await migration.apply()
        assert state(config, writer) == expected, f"store down after {after} reads"


async def test_qa12_5_an_unwritable_ledger_stops_apply_before_any_binding() -> None:
    config, writer, migration = world()

    async def refuse(key: str, value: Any) -> bool:  # noqa: ANN401
        return False

    config.set_config = refuse  # type: ignore[method-assign]
    before = copy.deepcopy(config.values)
    with pytest.raises(LedgerUnwritable):
        _ = await migration.apply()
    assert writer.rows == {} and config.values == before
    assert ORG not in {row["orgId"] for row in writer.rows.values()}
