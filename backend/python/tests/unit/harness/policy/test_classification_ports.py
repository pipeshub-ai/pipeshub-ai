"""OWD-15: a run's data classification and the source metadata connectors read are extension points
(``ClassificationPort``, ``SourceMetadataReader``) read through ``harness_edition()``. This build's defaults classify
nothing, record nothing beside a source and read no source metadata.

CLP-1..CLP-9.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override
from unittest.mock import MagicMock

import pytest

from app.connectors.core.base.source_metadata import (
    KEEP_HELD_METADATA,
    SourceMetadataReader,
    source_metadata,
)
from app.harness.classification import (
    NO_CLASSIFICATION,
    UNCLASSIFIED,
    ClassificationPort,
    NoClassification,
    PassthroughGuard,
    classification_guarded,
    classification_port,
    level_source,
    record_ranking,
    with_org_classification,
)
from app.harness.run_policy.ports import RunApprovals
from harness_core.edition import HarnessEdition
from harness_core.logs import HarnessLog
from harness_core.security.classification import UNCONFIGURED
from tests.support.harness_edition import default_harness_edition  # noqa: F401 - the fixture, used by name

pytestmark = pytest.mark.usefixtures("default_harness_edition")

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pytest

LOG = HarnessLog.named("test.classification_ports")
DOCUMENT = {"levels": ["public", "restricted"], "default": "restricted"}


@dataclass(frozen=True)
class _Levels:
    """A classification an edition's port made: this build reads only ``configured``."""

    levels: tuple[str, ...]
    configured: bool = True


CONFIGURED = _Levels(("public", "restricted"))


def test_clp1_the_default_classifies_nothing() -> None:
    port = classification_port()
    assert port is NO_CLASSIFICATION
    assert port.org_classification(DOCUMENT, LOG) is UNCONFIGURED
    assert isinstance(port.model_guard(LOG), PassthroughGuard)
    policy = MagicMock()
    assert port.approval_layer(policy, CONFIGURED, lambda: "restricted", LOG) is policy
    assert port.level_events(CONFIGURED, lambda: "restricted", lambda _feature: True) is None
    assert port.gateway(MagicMock()) is None
    assert port.declared_regions(MagicMock()) is None


def test_clp2_the_passthrough_guard_wraps_nothing_and_refuses_nothing() -> None:
    guard = PassthroughGuard()
    guard.bind(CONFIGURED, lambda: "restricted")
    model = object()
    assert guard.wrap(model, "gpt-x") is model  # type: ignore[arg-type]
    assert guard.level() is None
    assert guard.refusal("gpt-x", "openAI", reading=("rec",)) is None
    guard.check("gpt-x", "openAI")


def test_clp3_a_layer_with_a_document_leaves_the_runs_approvals_as_they_are() -> None:
    approvals = RunApprovals(rules=MagicMock())
    layer = MagicMock(carried={"rules": DOCUMENT})
    assert with_org_classification(approvals, layer, LOG) is approvals
    policy = MagicMock()
    assert classification_guarded(policy, UNCONFIGURED, ledger=None, run_id="r1", log=LOG) is policy


def test_clp4_an_editions_port_is_the_one_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Configured(NoClassification):
        @override
        def org_classification(self, document: Mapping[str, Any] | None, log: HarnessLog) -> Any:
            return _Levels(tuple(document["levels"])) if document is not None else UNCONFIGURED

        @override
        def live_classification(self, carried: Mapping[str, Any], log: HarnessLog) -> Any:
            return self.org_classification(carried.get("rules"), log)

    monkeypatch.setattr(
        "app.edition_services.harness_edition", lambda: HarnessEdition(ports={ClassificationPort: _Configured()})
    )
    approvals = with_org_classification(RunApprovals(rules=MagicMock()), MagicMock(carried={"rules": DOCUMENT}), LOG)
    assert approvals.classification.configured
    assert approvals.classification.levels == ("public", "restricted")


async def test_clp5_no_metadata_is_read_and_a_record_keeps_what_it_holds() -> None:
    assert source_metadata() is KEEP_HELD_METADATA
    asked: list[object] = []

    async def build(scopes: object) -> object:
        asked.append(scopes)
        return object()

    held = {"tag": "One"}
    drive = await KEEP_HELD_METADATA.drive_metadata(build, MagicMock())
    assert drive.fields_for({"labelInfo": {"labels": [{"id": "l1"}]}}, held) == held
    assert asked == [], "nothing is asked of Drive"
    source = object()
    assert KEEP_HELD_METADATA.drive_source(source, lambda: drive) is source

    def client() -> object:
        asked.append(("client", ""))
        return MagicMock()

    items = KEEP_HELD_METADATA.item_metadata(client, MagicMock())
    items.new_sync()
    assert await items.fields_for("d1", "i1", {}, changed=True) == {}
    assert await items.fields_for("d1", "i1", held, changed=True) == held
    assert asked == []
    assert isinstance(KEEP_HELD_METADATA, SourceMetadataReader)


async def test_clp6_box_confluence_and_mail_ask_for_nothing_and_keep_what_is_held() -> None:
    held = {"tag": "One"}
    assert KEEP_HELD_METADATA.box_fields() == ()
    assert KEEP_HELD_METADATA.box_metadata({"classification": {"name": "Two"}}, held) == held
    content = KEEP_HELD_METADATA.confluence_metadata(MagicMock())
    content.new_sync()
    datasource = MagicMock()
    assert await content.fields_for(datasource, "page", "1", "S1", held) == held
    assert datasource.mock_calls == []
    assert KEEP_HELD_METADATA.mail_expand() == ()
    assert KEEP_HELD_METADATA.mail_metadata(MagicMock(), {}) == {}


def test_clp9_no_classification_records_nothing_beside_a_source() -> None:
    record = {"id": "r1", "connector": "DRIVE", "editionFields": {"tag": "One"}}
    assert dict(NO_CLASSIFICATION.record_attributes(record, "DRIVE")) == {}
    from app.harness.labels import classify_record

    source = classify_record(record)
    assert source is not None and source != "web"
    assert dict(source.attributes) == {}


def test_clp6_box_reads_its_item_fields_and_outlook_expands_nothing() -> None:
    from app.connectors.sources.box.connector import ITEM_FIELDS, item_fields

    assert item_fields() == ITEM_FIELDS


def test_clp7_the_default_ranks_every_conversation_unclassified_and_lets_every_call_send_out() -> None:
    port = classification_port()
    assert port.level_source(CONFIGURED, None, "r1")() == UNCLASSIFIED
    assert port.level_source(CONFIGURED, MagicMock(), "r1", record_level=lambda _r: "restricted")() == UNCLASSIFIED
    assert port.record_ranking(CONFIGURED, MagicMock(), {"known_record_ids": ["rec"]})("rec") == UNCLASSIFIED
    assert port.egress(CONFIGURED, "restricted") == "allow"
    assert level_source(UNCONFIGURED, None, "r1")() == UNCLASSIFIED
    assert record_ranking(UNCONFIGURED)("rec") == UNCLASSIFIED


def test_clp8_the_live_layer_carries_no_member_beyond_its_own_here() -> None:
    """``LiveLayer.carried`` holds what an edition's overlay adds to the live layer; this repository's schema adds none."""
    import json

    from app.control_plane.runtime.live_layer import LIVE_LAYER_MEMBERS
    from tests.cohesion.support import REPO_ROOT

    schema = json.loads((REPO_ROOT / "contracts" / "control-plane" / "live-layer.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]) == LIVE_LAYER_MEMBERS
