"""What an edition records beside a checked source (``RecordRef.attributes``, ``ConnectorItem.attributes``): extra
wire fields of the item, which an edition's schema overlay declares. They are never part of a source's identity, a
source read with and without them keeps them, and the session ledger answers them by ``classified_id``.

SAT-1..SAT-4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.provenance.ledger import ProvenanceLedger
from harness_core.provenance.stamp import PROVENANCE_EVENT
from harness_core.provenance.types import (
    ConnectorItem,
    RecordRef,
    ResultProvenance,
    attributed,
    classified_id,
    source_from_wire,
)
from harness_core.testing.contracts.source_ledger import Stream

if TYPE_CHECKING:
    from pydantic import JsonValue

EXTRA: dict[str, JsonValue] = {"note": {"a": "b"}}
RUN = "run-1"


def test_sat1_attributes_ride_the_wire_beside_the_items_own_fields_and_read_back() -> None:
    record = RecordRef("r1", "indexed", "p1", attributes=EXTRA)
    wire = record.to_wire()
    assert wire == {"kind": "record", "recordId": "r1", "recordKind": "indexed", "publicationId": "p1", **EXTRA}
    back = source_from_wire(wire)
    assert isinstance(back, RecordRef) and dict(back.attributes) == EXTRA

    item = ConnectorItem("DRIVE", "ext-1", "c1", attributes=EXTRA)
    back_item = source_from_wire(item.to_wire())
    assert isinstance(back_item, ConnectorItem) and dict(back_item.attributes) == EXTRA


def test_sat2_attributes_never_shadow_an_items_own_fields_and_none_are_empty() -> None:
    record = RecordRef("r1", attributes={"recordId": "other", "kind": "web"})
    assert record.to_wire() == {"kind": "record", "recordId": "r1", "recordKind": "indexed"}
    plain = source_from_wire({"kind": "record", "recordId": "r1", "recordKind": "indexed"})
    assert isinstance(plain, RecordRef) and not plain.attributes


def test_sat3_attributes_are_not_identity_and_joining_keeps_them() -> None:
    bare, rich = RecordRef("r1"), RecordRef("r1", attributes=EXTRA)
    assert bare == rich and hash(bare) == hash(rich)
    joined = ResultProvenance("principals", "first_party", (bare,)).join(
        ResultProvenance("principals", "first_party", (rich,))
    )
    assert [dict(s.attributes) for s in joined.sources if isinstance(s, RecordRef)] == [EXTRA]
    assert attributed([bare, rich]) == {classified_id(rich): EXTRA}


def test_sat4_the_ledger_answers_each_sources_attributes() -> None:
    stream = Stream()
    stream.admitted(RUN)
    sources: list[JsonValue] = [
        RecordRef("r1", attributes=EXTRA).to_wire(),
        RecordRef("r2").to_wire(),
        ConnectorItem("DRIVE", "ext-1", attributes={"other": 1}).to_wire(),
    ]
    provenance: dict[str, JsonValue] = {"readers": "principals", "integrity": "first_party", "sources": sources}
    _ = stream.add(
        PROVENANCE_EVENT,
        {"invocationId": "inv-1", "toolId": "t", "rule": "records", "provenance": provenance},
        run_id=RUN,
    )
    ledger = ProvenanceLedger()
    ledger.apply(stream.events)
    assert {k: dict(v) for k, v in ledger.source_attributes().items()} == {
        "r1": EXTRA,
        classified_id(ConnectorItem("DRIVE", "ext-1")): {"other": 1},
    }
