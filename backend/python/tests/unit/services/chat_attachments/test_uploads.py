"""QA15G-1 (ERA-28): who uploaded a chat attachment, as the record names it."""

from __future__ import annotations

from typing import Any

import pytest

from app.schema.arango.documents import record_schema
from app.services.chat_attachments.uploads import (
    AttachmentFile,
    Uploader,
    UploaderKind,
    attachment_documents,
    recorded_uploader,
)

FILE = AttachmentFile(
    record_id="rec-1", virtual_record_id="vr-1", file_name="notes.txt", mime_type="text/plain", size=5,
    extension="txt", external_record_id="doc-1",
)


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"userId": "u1", "orgId": "o"}, Uploader("u1", UploaderKind.PERSON)),
        ({"userId": "u1", "isServiceAccount": False, "agentKey": "a"}, Uploader("u1", UploaderKind.PERSON)),
        (
            {"userId": "p1", "isServiceAccount": True, "agentKey": "agent-7"},
            Uploader("p1", UploaderKind.SERVICE_ACCOUNT, "agent-7"),
        ),
        ({"userId": "p1", "isServiceAccount": True}, Uploader("p1", UploaderKind.SERVICE_ACCOUNT)),
        ({"userId": "p1", "isServiceAccount": True, "agentKey": ""}, Uploader("p1", UploaderKind.SERVICE_ACCOUNT)),
    ],
)
def test_qa15g_1_the_uploader_is_the_person_and_for_a_service_account_its_agent(
    claims: dict[str, Any], expected: Uploader
) -> None:
    assert Uploader.from_claims(claims) == expected


@pytest.mark.parametrize("claims", [{}, {"userId": ""}, {"userId": None, "isServiceAccount": True}, {"userId": 7}])
def test_qa15g_1_an_upload_without_its_user_is_refused(claims: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Uploader.from_claims(claims)


def test_qa15g_1_the_record_names_its_uploader_and_reads_it_back() -> None:
    person, _ = attachment_documents("org-1", FILE, Uploader("u1", UploaderKind.PERSON), 1, tracked=True)
    service, _ = attachment_documents("org-1", FILE, Uploader("p1", UploaderKind.SERVICE_ACCOUNT, "agent-7"), 1, tracked=True)

    assert (person["uploaderUserId"], person["uploaderKind"], "uploaderAgentKey" in person) == ("u1", "person", False)
    assert (service["uploaderUserId"], service["uploaderKind"], service["uploaderAgentKey"]) == (
        "p1", "service_account", "agent-7",
    )
    assert (recorded_uploader(person), recorded_uploader(service)) == ("u1", "p1")
    assert recorded_uploader({"connectorName": "ATTACHMENTS"}) is None
    assert recorded_uploader({"uploaderUserId": ""}) is None


def test_qa15g_1_the_records_schema_admits_every_uploader_field_and_kind() -> None:
    properties = record_schema["rule"]["properties"]
    assert {kind.value for kind in UploaderKind} <= set(properties["uploaderKind"]["enum"])
    record, _ = attachment_documents("org-1", FILE, Uploader("p1", UploaderKind.SERVICE_ACCOUNT, "agent-7"), 1, tracked=True)
    assert set(record) - {"_key", "id"} <= set(properties)
