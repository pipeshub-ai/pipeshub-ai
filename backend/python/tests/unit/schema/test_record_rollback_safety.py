"""A record written with the off-by-default features off still fits the previous release's record schema.

The Arango record schema is strict and closed, so a release rolled back to refuses any update of a record carrying a
field it does not know. This release only accepts the fields it adds (``test_no_new_pipeline_fields``); these tests
prove that with the agent harness off and no edition reader, the records it writes carry none of them.
"""

from __future__ import annotations

import copy

import pytest
from jsonschema import Draft7Validator

from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import Record, RecordType
from app.schema.arango.documents import record_schema
from app.services.chat_attachments.uploads import (
    AttachmentFile,
    Uploader,
    UploaderKind,
    attachment_documents,
)

#: The record fields this release only accepts.
ACCEPTED_ONLY = frozenset({
    "awaitingSend", "editionFields", "uploadConversationId", "uploaderAgentKey", "uploaderKind", "uploaderUserId",
})

FILE = AttachmentFile(
    record_id="rec-1", virtual_record_id="vr-1", file_name="notes.txt", mime_type="text/plain", size=5,
    extension="txt", external_record_id="doc-1",
)
UPLOADERS = [Uploader("u1", UploaderKind.PERSON), Uploader("p1", UploaderKind.SERVICE_ACCOUNT, "agent-7")]


def _validator(schema: dict) -> Draft7Validator:
    return Draft7Validator(schema["rule"])


def _previous_release() -> Draft7Validator:
    schema = copy.deepcopy(record_schema)
    for field in ACCEPTED_ONLY:
        del schema["rule"]["properties"][field]
    schema["rule"]["required"] = [f for f in schema["rule"].get("required", []) if f not in ACCEPTED_ONLY]
    return _validator(schema)


def _stored(document: dict) -> dict:
    # Arango validates the stored body: the key is a system attribute and `id` becomes it on write.
    return {k: v for k, v in document.items() if k not in {"_key", "id"}}


def test_the_previous_schema_is_this_one_without_the_accepted_fields() -> None:
    assert ACCEPTED_ONLY <= set(record_schema["rule"]["properties"])
    assert record_schema["rule"]["additionalProperties"] is False
    assert record_schema["level"] == "strict"


@pytest.mark.parametrize("uploader", UPLOADERS, ids=lambda u: u.kind.value)
@pytest.mark.parametrize("conversation_id", [None, "conv-1"])
def test_a_chat_upload_with_the_harness_off_fits_the_previous_release(
    uploader: Uploader, conversation_id: str | None,
) -> None:
    record, _ = attachment_documents("org-1", FILE, uploader, 1, tracked=False, conversation_id=conversation_id)
    assert not list(_previous_release().iter_errors(_stored(record)))


@pytest.mark.parametrize("uploader", UPLOADERS, ids=lambda u: u.kind.value)
def test_a_chat_upload_with_the_harness_on_fits_only_this_release(uploader: Uploader) -> None:
    record, _ = attachment_documents("org-1", FILE, uploader, 1, tracked=True, conversation_id="conv-1")
    assert not list(_validator(record_schema).iter_errors(_stored(record)))
    assert list(_previous_release().iter_errors(_stored(record))), "the guard above would catch a new field"


def test_a_connector_record_without_edition_fields_fits_the_previous_release() -> None:
    record = Record(
        org_id="org-1",
        record_name="one.pdf",
        record_type=RecordType.FILE,
        external_record_id="ext-1",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE,
        connector_id="c1",
        mime_type="application/pdf",
    )
    stored = _stored(record.to_arango_base_record())
    assert not list(_previous_release().iter_errors(stored))
    record.edition_fields = {"tag": "One"}
    assert list(_previous_release().iter_errors(_stored(record.to_arango_base_record())))
