"""Graph identitySets, parsed by the real SDK, mapped to SourcePerson."""

import json
from typing import Any

from kiota_serialization_json.json_parse_node_factory import JsonParseNodeFactory
from msgraph.generated.models.drive_item import DriveItem

from app.connectors.sources.microsoft.common.graph_people import source_person
from app.models.entities import SourcePerson

ANA_ID = "4f1c2a9e-0b7d-4a51-9c3e-7d2b8f6a1e10"
APP_ID = "8a3e5b1c-2d4f-4e6a-b7c8-9d0e1f2a3b4c"


def parse_item(payload: dict[str, Any]) -> DriveItem:
    node = JsonParseNodeFactory().get_root_parse_node("application/json", json.dumps(payload).encode())
    return node.get_object_value(DriveItem)


def item(created_by: dict | None = None, last_modified_by: dict | None = None) -> DriveItem:
    payload: dict[str, Any] = {"id": "i1", "name": "plan.docx"}
    if created_by is not None:
        payload["createdBy"] = created_by
    if last_modified_by is not None:
        payload["lastModifiedBy"] = last_modified_by
    return parse_item(payload)


def test_a_user_with_an_email_keeps_the_entra_id_and_the_email() -> None:
    parsed = item(created_by={"user": {"id": ANA_ID, "displayName": "Ana Diaz", "email": "ana@acme.com"}})

    assert source_person(parsed.created_by) == SourcePerson(
        source_id=ANA_ID, email="ana@acme.com", display_name="Ana Diaz"
    )


def test_a_user_without_an_email_is_named_by_the_entra_id_only() -> None:
    parsed = item(created_by={"user": {"id": ANA_ID, "displayName": "Ana Diaz"}})

    person = source_person(parsed.created_by)

    assert person == SourcePerson(source_id=ANA_ID, display_name="Ana Diaz")
    assert person.identifiable


def test_an_application_without_a_user_is_a_service_account() -> None:
    parsed = item(last_modified_by={"application": {"id": APP_ID, "displayName": "Power Automate"}})

    person = source_person(parsed.last_modified_by)

    assert person == SourcePerson(source_id=APP_ID, display_name="Power Automate", is_service_account=True)
    assert not person.identifiable


def test_a_user_acting_through_an_application_is_the_user() -> None:
    parsed = item(last_modified_by={
        "application": {"id": APP_ID, "displayName": "Microsoft Teams"},
        "user": {"id": ANA_ID, "displayName": "Ana Diaz", "email": "ana@acme.com"},
    })

    assert source_person(parsed.last_modified_by) == SourcePerson(
        source_id=ANA_ID, email="ana@acme.com", display_name="Ana Diaz"
    )


def test_sharepoint_system_account_is_a_service_account() -> None:
    parsed = item(created_by={"user": {"displayName": "System Account"}})

    person = source_person(parsed.created_by)

    assert person is not None and person.is_service_account
    assert not person.identifiable


def test_a_user_with_neither_id_nor_email_names_nobody() -> None:
    parsed = item(created_by={"user": {"displayName": "Ana Diaz"}})

    assert source_person(parsed.created_by) is None


def test_no_identity_set_names_nobody() -> None:
    assert source_person(item().created_by) is None
    assert source_person(None) is None
