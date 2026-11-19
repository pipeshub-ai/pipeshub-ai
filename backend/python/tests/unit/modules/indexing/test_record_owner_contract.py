"""Code that reads whose connector a record belongs to goes through ``RecordOwnership``.

A record may index on behalf of another, so a raw read of its connector in the indexing paths answers for the wrong one.
The scan below fails when a new raw read appears without an entry (and a reason) in ``ALLOWED``, and when an entry stops
matching anything. The behaviour tests install an ownership that answers for derived records and check each converted site.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import OriginTypes, ProgressStatus
from app.modules.indexing import extension
from app.modules.indexing.connector_off_events import gating_connector_id
from app.modules.indexing.extension import DefaultRecordOwnership, IndexingExtension
from app.modules.indexing.record_republish import republish_eligible

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

APP = Path(__file__).resolve().parents[4] / "app"
SCANNED = [
    APP / "modules" / "indexing",
    APP / "services" / "messaging",
    APP / "modules" / "pipeline",
    APP / "events",
    APP / "indexing_main.py",
]
RAW_READ = re.compile(r"""get\(["']connectorId["']\)|\[["']connectorId["']\]|\.connector_id\b""")

# (file suffix, regex the line must match, why the raw read is right)
ALLOWED: list[tuple[str, str, str]] = [
    ("modules/indexing/extension.py", r"record\.get\(\"connectorId\"\)", "the default ownership: a record belongs to its own connector"),
    ("modules/indexing/run.py", r"ctx\.connector_id", "a connector-scoped run names the connector it was started for, not a record"),
    ("modules/indexing/entity_index_rebuild.py", r"intent\[", "an intent row names the connector it rebuilds"),
    ("modules/indexing/connector_off_events.py", r"payload", "envelope of an event, which already names its owner"),
    ("services/messaging/message_log.py", r"payload", "envelope"),
    ("services/messaging/connector_off.py", r"payload", "envelope"),
    ("services/messaging/lanes/assignment.py", r"request\.connector_id", "a lane request is built from the envelope's key"),
    ("services/messaging/kafka/handlers/entity.py", r"payload|\(value or \{\}\)", "envelope of app events"),
    ("services/messaging/kafka/utils/utils.py", r"payload", "envelope"),
    ("services/messaging/kafka/handlers/record.py", r"payload", "envelope"),
    ("services/messaging/kafka/handlers/record.py", r"record\.get\(\"connectorId\"\)|record\[\"connectorId\"\]",
     "rate-limit deferral: guarded by origin == CONNECTOR, which a derived record never has"),
    ("modules/pipeline/worker.py", r"job\.connector_id", "a job's connector comes from the RecordView, built with the ownership"),
    ("modules/pipeline/coordinator.py", r"job\.connector_id", "as above"),
    ("modules/pipeline/models.py", r"(self|job|view)\.connector_id", "as above"),
    ("modules/pipeline/stages/record_io.py", r"job\.connector_id", "as above"),
    ("modules/indexing/record_title_consumer.py", r"get\(\"connectorId\"\)",
     "titles are for records of live connectors; a record that has no connector of its own is skipped"),
    ("modules/indexing/class_units_backfill.py", r"row\.get\(\"connectorId\"\)",
     "class-unit projection of an indexed row, which reads the row's own connector"),
    ("events/events.py", r"(record_doc|doc)\.get\(\"connectorId\"\)",
     "code-graph projection and the stored block list of a record: both are scoped by the record's own connector"),
    ("events/processor.py", r"record\.connector_id", "code-graph projection of a code file"),
    ("indexing_main.py", r"record\.get\(\"connectorId\"\)", "the event payload rebuilt for a recovered record carries the record's own connector"),
]


def _hits() -> list[tuple[str, int, str]]:
    found = []
    for root in SCANNED:
        for path in sorted([root] if root.is_file() else root.rglob("*.py")):
            rel = path.relative_to(APP).as_posix()
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if RAW_READ.search(line):
                    found.append((rel, number, line.strip()))
    return found


def test_every_raw_connector_read_is_allowed_with_a_reason() -> None:
    unexplained = [
        f"{rel}:{number}: {line}"
        for rel, number, line in _hits()
        if not any(rel.endswith(suffix) and re.search(pattern, line) for suffix, pattern, _ in ALLOWED)
    ]
    assert not unexplained, "read the owner through RecordOwnership, or allow-list with a reason:\n" + "\n".join(unexplained)


def test_every_allow_list_entry_still_matches_a_line() -> None:
    hits = _hits()
    stale = [
        (suffix, pattern) for suffix, pattern, _ in ALLOWED
        if not any(rel.endswith(suffix) and re.search(pattern, line) for rel, _, line in hits)
    ]
    assert not stale


def test_the_scan_flags_a_raw_read_it_has_not_been_told_about() -> None:
    """The negative control: a line the allow-list does not cover is reported by the same predicate."""
    line = 'connector_id = record.get("connectorId")'
    assert RAW_READ.search(line)
    assert not any(re.search(pattern, line) for suffix, pattern, _ in ALLOWED if "new_module.py".endswith(suffix))


# ---- the converted sites, with an ownership that answers for derived records ---------------------

DERIVED = {"_key": "d1", "connectorId": "hidden", "origin": OriginTypes.UPLOAD.value, "derived": True}


class Derived(DefaultRecordOwnership):
    """Derived records answer to ``owner-1`` and are gated, republished and laned like its own."""

    primed: list[str]

    def __init__(self) -> None:
        self.primed = []

    def owner_connector_id(self, record: object) -> str | None:
        if isinstance(record, dict) and record.get("derived"):
            return "owner-1"
        return super().owner_connector_id(record)

    def is_derived(self, record: object) -> bool:
        return isinstance(record, dict) and bool(record.get("derived"))

    def has_lane(self, connector_id: str) -> bool:
        return connector_id != "hidden"

    async def prime(self, records: list, graph: object) -> None:
        self.primed += [r["_key"] for r in records]

    async def prime_readable(self, page: list, graph: object, skips: object, logger: object) -> list:
        return [r for r in page if r.get("_key") != "unreadable"]

    async def derived_pages(
        self, graph: object, connector_id: str, org_id: str, filters: object, *, page_size: int
    ) -> AsyncIterator[list]:
        yield [{**DERIVED, "_key": f"extra-{connector_id}"}]


@pytest.fixture
def derived() -> Derived:
    own = Derived()
    extension.install(IndexingExtension(ownership=own))
    return own


def test_without_an_extension_a_record_is_gated_by_its_own_connector() -> None:
    assert gating_connector_id({"connectorId": "drive-1", "origin": OriginTypes.CONNECTOR.value}) == "drive-1"
    assert gating_connector_id({"connectorId": "kb-1", "origin": OriginTypes.UPLOAD.value}) is None
    assert gating_connector_id(DERIVED) is None


def test_a_derived_record_is_gated_by_its_owner(derived: Derived) -> None:
    assert gating_connector_id(DERIVED) == "owner-1"
    assert gating_connector_id({"connectorId": "kb-1", "origin": OriginTypes.UPLOAD.value}) is None


@pytest.mark.parametrize("status", [ProgressStatus.QUEUED.value, ProgressStatus.NOT_STARTED.value])
def test_a_waiting_derived_record_is_eligible_for_the_stranded_sweep_only_with_an_ownership_that_says_so(status: str) -> None:
    assert republish_eligible({**DERIVED, "isDeleted": False}, status)[0] is False

    extension.install(IndexingExtension(ownership=Derived()))

    assert republish_eligible({**DERIVED, "isDeleted": False}, status)[0] is True


async def test_lane_upkeep_never_frees_or_reads_a_connector_with_no_lane(derived: Derived) -> None:
    from app.modules.indexing.lane_upkeep import _reconcile_with_graph

    entry = MagicMock(is_live=True)
    assignments = MagicMock()
    assignments.release = AsyncMock(return_value=1)
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=None)

    released, corrected = await _reconcile_with_graph(
        assignments, graph, {"hidden": entry}, {}, logging.getLogger("t"),  # type: ignore[arg-type]
    )

    assert (released, corrected) == (0, 0)
    assignments.release.assert_not_awaited()
    graph.get_document.assert_not_awaited()


def test_a_derived_record_is_never_a_duplicate_of_anything_and_has_none(derived: Derived) -> None:
    from app.events.dedup import select_duplicate, will_promote_queued_copies

    twin = {"_key": "t", "indexingStatus": ProgressStatus.COMPLETED.value, "virtualRecordId": "v"}
    resolve = lambda _r: "records"  # noqa: E731

    assert select_duplicate([twin], "records", resolve) is not None
    assert select_duplicate([twin], "records", resolve, current=DERIVED) is None
    assert select_duplicate([DERIVED, twin], "records", resolve).record is twin  # type: ignore[union-attr]
    assert will_promote_queued_copies({"indexingStatus": ProgressStatus.QUEUED.value}, None) is True
    assert will_promote_queued_copies({**DERIVED, "indexingStatus": ProgressStatus.QUEUED.value}, None) is False


def test_a_derived_record_is_not_classified_on_first_use_and_names_its_owner(derived: Derived) -> None:
    from app.modules.pipeline.first_use import first_use_event, unclassified

    own = {
        "_key": "a", "indexingStatus": ProgressStatus.COMPLETED.value, "extractionStatus": ProgressStatus.NOT_STARTED.value,
        "virtualRecordId": "v", "contentRev": "r", "connectorId": "drive-1",
    }
    other = {**own, "_key": "d1", "derived": True, "connectorId": "hidden"}

    assert unclassified([own, other]) == [own]
    assert first_use_event(other, "o1")["payload"]["connectorId"] == "owner-1"
    assert first_use_event(own, "o1")["payload"]["connectorId"] == "drive-1"


async def test_parse_now_primes_the_record_before_it_is_gated(derived: Derived) -> None:
    from app.modules.pipeline.parse_now import ParseNow

    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"isActive": True, "status": "ACTIVE"})
    parse_now = ParseNow(config_service=MagicMock(), logger=logging.getLogger("t"))
    doc = {**DERIVED, "indexingStatus": ProgressStatus.NOT_STARTED.value, "origin": OriginTypes.CONNECTOR.value}

    await parse_now.assess(graph, doc)

    assert derived.primed == ["d1"]
