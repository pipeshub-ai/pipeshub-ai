"""``ArtifactPropertiesReader`` against the storage fake the artifact tests use, and the app documents the
sweep maps to connectors."""

from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import Connectors
from app.core.org_context import for_org
from app.models.entities import RecordType, Status, TicketRecord
from app.modules.artifacts.models import PROPERTIES_PATH
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.indexing.property_projection_sweep import (
    ArtifactPropertiesReader,
    PropertiesUnavailable,
    connector_of_app,
)
from app.modules.record_metadata.builtin import builtin_properties
from tests.unit.modules.artifacts.fake_storage import FakeStorage
from tests.unit.modules.artifacts.test_artifact_reader import (
    ORG,
    REV,
    VRID,
    _key,
    _Mappings,
    _record,
    _write,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _in_the_org_of_the_scope() -> "Iterator[None]":
    """The sweep reads a scope's records in its org (the reader takes the org from the context, not from the record)."""
    with for_org(ORG, job="test"):
        yield


@pytest.fixture
async def storage() -> "AsyncIterator[FakeStorage]":
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


def _reader(storage: FakeStorage, lookup: dict[str, Any] | None) -> ArtifactPropertiesReader:
    mappings = _Mappings({VRID: lookup} if lookup else {})
    return ArtifactPropertiesReader(mappings, NodeArtifactStore(storage.blob_storage()))


def _doc(**over: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"_key": "rec-1", "virtualRecordId": VRID, "orgId": ORG, **over}


def _ticket() -> TicketRecord:
    return TicketRecord(**{
        **dict(_record()), "record_type": RecordType.TICKET, "connector_name": Connectors.JIRA,
        "status": Status.OPEN, "assignee": "Ada", "labels": ["auth"],
    })


async def test_a_revision_with_properties_reads_them_back(storage: FakeStorage) -> None:
    lookup = await _write(storage, _ticket())
    metadata = await _reader(storage, lookup)(_doc(contentRev=REV))
    assert metadata is not None and metadata.values["status"].value == "OPEN"


async def test_a_revision_without_the_file_holds_no_values(storage: FakeStorage) -> None:
    lookup = await _write(storage, _record())
    assert _key(PROPERTIES_PATH) not in storage.puts
    assert await _reader(storage, lookup)(_doc()) is None


@pytest.mark.parametrize(("lookup", "reason"), [
    (None, "no_pointer"),
    ({"currentRev": REV}, "no_pointer"),
])
async def test_no_pointer_is_unavailable_not_empty(storage: FakeStorage, lookup: Any, reason: str) -> None:  # noqa: ANN401
    with pytest.raises(PropertiesUnavailable, match=reason):
        await _reader(storage, lookup)(_doc())


async def test_a_record_on_another_revision_is_unavailable(storage: FakeStorage) -> None:
    lookup = await _write(storage, _ticket())
    with pytest.raises(PropertiesUnavailable, match="other_revision"):
        await _reader(storage, lookup)(_doc(contentRev="ffffffffffffffff"))


async def test_a_corrupt_file_is_unavailable(storage: FakeStorage) -> None:
    lookup = await _write(storage, _ticket())
    stored = storage.puts[_key(PROPERTIES_PATH)]
    stored.data = stored.data[:-3] + b"xyz"
    with pytest.raises(PropertiesUnavailable, match="integrity"):
        await _reader(storage, lookup)(_doc(contentRev=REV))


async def test_a_missing_manifest_is_unavailable(storage: FakeStorage) -> None:
    from app.modules.artifacts.models import MANIFEST_PATH

    lookup = await _write(storage, _ticket())
    del storage.puts[_key(MANIFEST_PATH)]
    with pytest.raises(PropertiesUnavailable):
        await _reader(storage, lookup)(_doc(contentRev=REV))


async def test_a_record_without_a_virtual_record_cannot_be_read(storage: FakeStorage) -> None:
    with pytest.raises(PropertiesUnavailable, match="no_virtual_record"):
        await _reader(storage, None)({"_key": "x", "orgId": ORG})


@pytest.mark.parametrize("registered", ["Jira", "Linear", "Salesforce", "Zammad", "Zendesk"])
def test_the_names_ticket_connectors_register_under_map_to_the_ticket_schema(registered: str) -> None:
    """App documents carry the registered display name as `type` (tests/integration/*: "type": "Jira")."""
    connector = connector_of_app({"type": registered})
    assert connector is not None and builtin_properties(connector) is not None


@pytest.mark.parametrize("registered", ["Confluence", "Drive", "Slack", "Notion", "Web"])
def test_other_connectors_map_to_no_built_in_schema(registered: str) -> None:
    connector = connector_of_app({"type": registered})
    assert connector is None or builtin_properties(connector) is None
