"""The ``RunConfigPinner`` reference adapter and its contract suite (RCP, 03-interfaces §4.4; CPD-5).

``RunConfigPinnerContract``: provide ``pinner_factory``, a callable taking a snapshot source (``(org_id, agent_key,
actor_id) -> PinSnapshot``) and returning the pinner under test. ``tests/unit/control_plane/test_pin_vectors.py``
runs it on ``InMemoryRunConfigPinner``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, cast

import pytest

from app.control_plane.contracts.generated.problem import Problem
from app.control_plane.contracts.generated.resolved_run_config import ResolvedRunConfig
from app.control_plane.runtime.pinning import (
    PinSnapshot,
    PinSubject,
    assemble,
    inherited_dimensions,
    pin_layers,
    pin_scope,
    refusal_problem,
)
from harness_core.contracts import canonical_digest, dump_contract
from harness_core.policy.resolver import DimensionPolicyResolver

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.control_plane.runtime.ports import RunConfigPinner

SnapshotSource = Callable[[str, str | None, str], Awaitable[PinSnapshot]]

EMPTY_SET: Mapping[str, JsonValue] = {"items": [], "digest": canonical_digest([])}


class InMemoryRunConfigPinner:
    """Folds release, the snapshot's layers and the subject's through the generic resolver. The effective set is the
    empty set: Python resolves effective sets for vectors only (CPD-7), and routing is the parent's for a child,
    durable otherwise (the host decides routing before it pins)."""

    def __init__(
        self, snapshots: SnapshotSource, resolver: DimensionPolicyResolver | None = None
    ) -> None:
        self._snapshots = snapshots
        self._resolver = resolver if resolver is not None else DimensionPolicyResolver()

    async def pin(self, subject: PinSubject) -> ResolvedRunConfig | Problem:
        snapshot = await self._snapshots(
            subject.org_id, subject.agent_key, subject.actor_id
        )
        resolved = self._resolver.resolve(pin_layers(subject, snapshot))
        if resolved.refusal is not None:
            return refusal_problem(resolved.refusal)
        parent = subject.parent
        config: Mapping[str, JsonValue] = cast("Mapping[str, JsonValue]", resolved.config)
        if parent is not None:
            config = inherited_dimensions(config, cast("Mapping[str, JsonValue]", dump_contract(parent.effective_config)))
        routing: JsonValue = (
            cast("JsonValue", dump_contract(subject.parent.routing))
            if subject.parent is not None
            else {"path": "durable", "reason": None}
        )
        return assemble(
            {
                "schemaVersion": 1,
                "headRevision": snapshot.head_revision,
                "routing": routing,
                "scope": pin_scope(subject),
                "effectiveSet": dict(EMPTY_SET),
                "effectiveConfig": cast("JsonValue", config),
                # A child calls the model its parent was pinned to; a root's is the host's to read.
                "llm": None if parent is None or parent.llm is None else cast("JsonValue", dump_contract(parent.llm)),
                "deploymentDigest": canonical_digest(dict(snapshot.deployment or {})),
            }
        )


ORG = "o1"


def _layer(kind: str, **sections: JsonValue) -> dict[str, JsonValue]:
    return {"schemaVersion": 1, "scope": {"kind": kind}, **sections}


def _snapshot(head: int = 41) -> PinSnapshot:
    return PinSnapshot(
        head_revision=head,
        layers={
            f"org:{ORG}": _layer("org", budgets={"maxCostUsd": 5}),
            "agent:a7": _layer("agent", budgets={"maxCostUsd": 3, "maxTurns": 10}),
        },
    )


def _subject(**overrides: object) -> PinSubject:
    fields: dict[str, object] = {
        "org_id": ORG,
        "actor_id": "u3",
        "agent_key": "a7",
        "chat_mode": "quick",
        "surface": "web",
        "request": _layer("request"),
    }
    fields.update(overrides)
    return PinSubject(**fields)  # type: ignore[arg-type]


def _pinned(result: ResolvedRunConfig | Problem) -> ResolvedRunConfig:
    assert isinstance(result, ResolvedRunConfig), result
    return result


def _value(pin: ResolvedRunConfig, path: str) -> object:
    config = cast(
        "Mapping[str, Mapping[str, Mapping[str, object]]]",
        dump_contract(pin.effective_config),
    )
    return config["dimensions"][path]["value"]


class RunConfigPinnerContract:
    """RCP-1, RCP-2, RCP-4 (CP-SKEL §1.4) for a Python pinner."""

    @pytest.fixture
    def pinner_factory(self) -> Callable[[SnapshotSource], RunConfigPinner]:
        raise NotImplementedError

    @staticmethod
    def _source(snapshot: PinSnapshot) -> SnapshotSource:
        async def read(_org: str, _agent: str | None, _actor: str) -> PinSnapshot:
            return snapshot

        return read

    async def test_rcp_1_same_subject_and_snapshot_pin_the_same_digest(
        self, pinner_factory: Callable[[SnapshotSource], RunConfigPinner]
    ) -> None:
        first = _pinned(await pinner_factory(self._source(_snapshot())).pin(_subject()))
        second = _pinned(
            await pinner_factory(self._source(_snapshot())).pin(_subject())
        )
        assert first.digest == second.digest
        assert dump_contract(first)["headRevision"] == 41
        assert _value(first, "budgets.maxCostUsd") == 3

    async def test_rcp_2_a_child_is_at_most_its_parent(
        self, pinner_factory: Callable[[SnapshotSource], RunConfigPinner]
    ) -> None:
        pinner = pinner_factory(self._source(_snapshot()))
        parent = _pinned(await pinner.pin(_subject()))
        for asked in (1, 3):
            child = _pinned(
                await pinner.pin(
                    _subject(
                        surface="child",
                        parent=parent,
                        request=_layer("request", budgets={"maxCostUsd": asked}),
                    )
                )
            )
            assert cast("float", _value(child, "budgets.maxCostUsd")) <= 3
            assert cast("int", _value(child, "budgets.maxTurns")) <= 10
            assert child.routing == parent.routing
        widened = await pinner.pin(
            _subject(
                surface="child",
                parent=parent,
                request=_layer("request", budgets={"maxTurns": 11}),
            )
        )
        assert isinstance(widened, Problem)
        refused = cast("Mapping[str, object]", dump_contract(widened))
        assert (refused["code"], refused["dimension"]) == (
            "CP_WIDENING",
            "budgets.maxTurns",
        )

    async def test_rcp_4_a_widening_request_is_refused_naming_dimension_and_layer(
        self, pinner_factory: Callable[[SnapshotSource], RunConfigPinner]
    ) -> None:
        result = await pinner_factory(self._source(_snapshot())).pin(
            _subject(request=_layer("request", budgets={"maxCostUsd": 4}))
        )
        assert isinstance(result, Problem)
        refused = cast("Mapping[str, object]", dump_contract(result))
        assert (
            refused["code"],
            refused["status"],
            refused["dimension"],
            refused["layer"],
        ) == (
            "CP_WIDENING",
            422,
            "budgets.maxCostUsd",
            "request",
        )


__all__ = ["InMemoryRunConfigPinner", "RunConfigPinnerContract", "SnapshotSource"]
