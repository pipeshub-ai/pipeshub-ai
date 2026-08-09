"""`StateSlot.to_json`/`from_json` (task engine plan Part C2/Phase 6: "first
production use of `StateSlot(persist=True)`" -- see that phase's own risk
note in the plan's Part L). Before this, `RunScope.snapshot_extensions()`/
`restore_extensions()` round-tripped a `persist=True` slot's value as-is,
which only works for slots holding plain JSON-safe dicts/lists/primitives
-- a slot holding a Pydantic model (or a dict of dataclasses wrapping one,
like `SPAWN_RESULTS_SLOT`) would come back from a checkpoint as a raw dict,
not its declared runtime type, silently breaking every consumer that calls
an attribute on it. These tests cover the generic mechanism in isolation,
before either real persisted slot (`STRUCTURED_PLAN_SLOT`,
`SPAWN_RESULTS_SLOT`) is exercised end-to-end elsewhere.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.agent_loop_lib.core.scope import RunScope, StateSlot, known_persisted_slots


class _Widget(BaseModel):
    name: str
    count: int = 0


def _make_scope(spec: object = None, runtime: object = None) -> RunScope:
    from app.agent_loop_lib.core.types import Goal

    return RunScope(identity=object(), spec=spec, runtime=runtime, goal=Goal(description="test"))  # type: ignore[arg-type]


class TestSlotWithoutJsonHooksRoundTripsRawValue:
    def test_plain_dict_slot_survives_snapshot_and_restore(self) -> None:
        slot: StateSlot[dict] = StateSlot(key="test.plain_dict", default_factory=dict, persist=True)
        scope = _make_scope()
        scope.set(slot, {"a": 1})

        snapshot = scope.snapshot_extensions()
        assert snapshot == {"test.plain_dict": {"a": 1}}

        restored = _make_scope()
        restored.restore_extensions(snapshot, known_slots=[slot])
        assert restored.get(slot) == {"a": 1}


class TestSlotWithJsonHooksReconstructsRealType:
    def test_pydantic_model_value_survives_round_trip_as_the_real_type(self) -> None:
        slot: StateSlot[_Widget | None] = StateSlot(
            key="test.widget",
            default_factory=lambda: None,
            persist=True,
            to_json=lambda w: w.model_dump(mode="json") if w is not None else None,
            from_json=lambda raw: _Widget.model_validate(raw) if raw is not None else None,
        )
        scope = _make_scope()
        scope.set(slot, _Widget(name="gizmo", count=3))

        snapshot = scope.snapshot_extensions()
        # The checkpoint payload itself must be a plain JSON-safe dict, not
        # a live Pydantic instance -- this is what a real `CheckpointStore`
        # would actually serialize to disk/DB.
        assert snapshot == {"test.widget": {"name": "gizmo", "count": 3}}

        restored = _make_scope()
        restored.restore_extensions(snapshot, known_slots=[slot])
        value = restored.get(slot)
        assert isinstance(value, _Widget)
        assert value.name == "gizmo"
        assert value.count == 3

    def test_none_value_round_trips_as_none_not_a_missing_key(self) -> None:
        slot: StateSlot[_Widget | None] = StateSlot(
            key="test.widget_none",
            default_factory=lambda: None,
            persist=True,
            to_json=lambda w: w.model_dump(mode="json") if w is not None else None,
            from_json=lambda raw: _Widget.model_validate(raw) if raw is not None else None,
        )
        scope = _make_scope()
        scope.set(slot, None)

        snapshot = scope.snapshot_extensions()
        assert snapshot == {"test.widget_none": None}

        restored = _make_scope()
        restored.restore_extensions(snapshot, known_slots=[slot])
        assert restored.get(slot) is None


class TestNonPersistedSlotIsNeverInSnapshot:
    def test_persist_false_slot_is_excluded(self) -> None:
        slot: StateSlot[int] = StateSlot(key="test.not_persisted", default_factory=int, persist=False)
        scope = _make_scope()
        scope.set(slot, 42)
        assert scope.snapshot_extensions() == {}


class TestUnknownSnapshotKeyIsDroppedOnRestore:
    def test_restore_ignores_keys_with_no_matching_known_slot(self) -> None:
        scope = _make_scope()
        # No exception, no partial state -- matches "unknown field" policy
        # documented on `restore_extensions`.
        scope.restore_extensions({"some.slot.that.no.longer.exists": {"x": 1}}, known_slots=())
        assert scope.snapshot_extensions() == {}


class TestRealPersistedSlotsRegisterThemselves:
    def test_structured_plan_and_spawn_results_slots_are_known_persisted_slots(self) -> None:
        from app.agent_loop_lib.agent.spawn_scheduler import SPAWN_RESULTS_SLOT
        from app.agent_loop_lib.modules.pipeline.planner.base import (
            STRUCTURED_PLAN_SLOT,
        )

        known = known_persisted_slots()
        assert STRUCTURED_PLAN_SLOT in known
        assert SPAWN_RESULTS_SLOT in known
