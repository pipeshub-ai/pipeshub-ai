# ruff: noqa
"""
HubSpot CRM SDK Code Generator

Generates ``HubSpotDataSource`` class that wraps the official ``hubspot-api-client``
Python SDK.  Each method calls the SDK directly and wraps the result in a
``HubSpotResponse``.

Pattern follows the GitLab code-generator approach: a list of (signature, body,
docstring) tuples that are emitted into a single class.

SDK reference (hubspot-api-client):
- Contacts:   client.crm.contacts.basic_api.get_page / get_by_id / create / update / archive
- Companies:  client.crm.companies.basic_api.*
- Deals:      client.crm.deals.basic_api.*
- Tickets:    client.crm.tickets.basic_api.*
- Notes:      client.crm.objects.basic_api.* (object_type="notes")
- Pipelines:  client.crm.pipelines.pipelines_api.get_all / get_by_id
- Properties: client.crm.properties.core_api.get_all
- Owners:     client.crm.owners.owners_api.get_page / get_by_id
- Search:     client.crm.<object>.search_api.do_search

Usage:
    cd backend/python
    python code-generator/hubspot.py

Output:
    app/sources/external/hubspot/hubspot.py
"""
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_RESPONSE_IMPORT = (
    "from app.sources.client.hubspot.hubspot import HubSpotResponse"
)
DEFAULT_CLASS_NAME = "HubSpotDataSource"
DEFAULT_OUT = str(
    Path(__file__).resolve().parent.parent
    / "app"
    / "sources"
    / "external"
    / "hubspot"
    / "hubspot.py"
)

# ---------------------------------------------------------------------------
# Header / Footer templates
# ---------------------------------------------------------------------------

HEADER = '''\
# ruff: noqa
"""
HubSpot CRM SDK DataSource - Auto-generated wrapper

Generated from hubspot-api-client Python SDK.
All methods call the SDK directly and wrap results in HubSpotResponse.
"""
from __future__ import annotations

from typing import Any, Optional

from hubspot.crm.contacts import SimplePublicObjectInputForCreate as ContactCreateInput  # type: ignore[import-untyped]
from hubspot.crm.contacts import SimplePublicObjectInput as ContactUpdateInput  # type: ignore[import-untyped]
from hubspot.crm.companies import SimplePublicObjectInputForCreate as CompanyCreateInput  # type: ignore[import-untyped]
from hubspot.crm.companies import SimplePublicObjectInput as CompanyUpdateInput  # type: ignore[import-untyped]
from hubspot.crm.deals import SimplePublicObjectInputForCreate as DealCreateInput  # type: ignore[import-untyped]
from hubspot.crm.deals import SimplePublicObjectInput as DealUpdateInput  # type: ignore[import-untyped]
from hubspot.crm.tickets import SimplePublicObjectInputForCreate as TicketCreateInput  # type: ignore[import-untyped]
from hubspot.crm.objects.notes import SimplePublicObjectInputForCreate as NoteCreateInput  # type: ignore[import-untyped]
from hubspot.crm.contacts import PublicObjectSearchRequest as ContactSearchRequest  # type: ignore[import-untyped]
from hubspot.crm.companies import PublicObjectSearchRequest as CompanySearchRequest  # type: ignore[import-untyped]
from hubspot.crm.deals import PublicObjectSearchRequest as DealSearchRequest  # type: ignore[import-untyped]

{response_import}


def _to_dict(obj: object) -> Any:
    """Convert an SDK response object to a plain dict/list."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()  # type: ignore[reportUnknownMemberType]
    return obj


class {class_name}:
    """HubSpot CRM SDK DataSource

    Typed wrapper around the official ``hubspot-api-client`` SDK for common
    CRM operations:
    - Contacts CRUD and search
    - Companies CRUD and search
    - Deals CRUD and search
    - Tickets CRUD
    - Notes/Engagements CRUD
    - Pipelines and pipeline stages
    - Properties management
    - Owners management

    Accepts a ``HubSpotClient`` (which exposes ``.get_sdk() -> HubSpot``) or
    a raw ``HubSpot`` SDK instance.

    All methods return ``HubSpotResponse`` objects.
    """

    def __init__(self, client_or_sdk: object) -> None:
        """Initialize with a HubSpotClient or raw HubSpot SDK instance.

        Args:
            client_or_sdk: A ``HubSpotClient`` with ``.get_sdk()`` or a
                ``HubSpot`` instance directly.
        """
        if hasattr(client_or_sdk, "get_sdk"):
            self._sdk: Any = client_or_sdk.get_sdk()  # type: ignore[reportUnknownMemberType]
        else:
            self._sdk = client_or_sdk

    def get_data_source(self) -> "{class_name}":
        """Return the data source instance."""
        return self

'''

FOOTER = ""

# ---------------------------------------------------------------------------
# Method definitions: (signature, body, docstring)
# ---------------------------------------------------------------------------

METHODS: List[Tuple[str, str, str]] = []

# ========== CONTACTS ==========
METHODS += [
    (
        "list_contacts(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}\n"
        "            if after is not None:\n"
        "                kwargs['after'] = after\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.contacts.basic_api.get_page(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed contacts')",
        "List contacts with pagination and optional property selection.",
    ),
    (
        "get_contact(self, contact_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'contact_id': contact_id, 'archived': archived}\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.contacts.basic_api.get_by_id(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved contact')",
        "Get a single contact by ID.",
    ),
    (
        "create_contact(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse",
        "            body = ContactCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.contacts.basic_api.create(simple_public_object_input_for_create=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created contact')",
        "Create a new contact with the given properties.",
    ),
    (
        "update_contact(self, contact_id: str, properties: dict[str, str]) -> HubSpotResponse",
        "            body = ContactUpdateInput(properties=properties)  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.contacts.basic_api.update(contact_id=contact_id, simple_public_object_input=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully updated contact')",
        "Update an existing contact's properties.",
    ),
    (
        "delete_contact(self, contact_id: str) -> HubSpotResponse",
        "            self._sdk.crm.contacts.basic_api.archive(contact_id=contact_id)\n"
        "            return HubSpotResponse(success=True, data={'archived': True}, message='Successfully archived contact')",
        "Archive (soft-delete) a contact by ID.",
    ),
    (
        "search_contacts(self, filter_groups: Optional[list[dict[str, Any]]] = None, query: Optional[str] = None, properties: Optional[list[str]] = None, sorts: Optional[list[dict[str, Any]]] = None, limit: int = 10, after: int = 0) -> HubSpotResponse",
        "            request_body: dict[str, Any] = {'limit': limit, 'after': after}\n"
        "            if filter_groups is not None:\n"
        "                request_body['filter_groups'] = filter_groups\n"
        "            if query is not None:\n"
        "                request_body['query'] = query\n"
        "            if properties is not None:\n"
        "                request_body['properties'] = properties\n"
        "            if sorts is not None:\n"
        "                request_body['sorts'] = sorts\n"
        "            search_request = ContactSearchRequest(**request_body)  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.contacts.search_api.do_search(public_object_search_request=search_request)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully searched contacts')",
        "Search contacts using filter groups, query string, and sorting.",
    ),
]

# ========== COMPANIES ==========
METHODS += [
    (
        "list_companies(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}\n"
        "            if after is not None:\n"
        "                kwargs['after'] = after\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.companies.basic_api.get_page(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed companies')",
        "List companies with pagination and optional property selection.",
    ),
    (
        "get_company(self, company_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'company_id': company_id, 'archived': archived}\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.companies.basic_api.get_by_id(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved company')",
        "Get a single company by ID.",
    ),
    (
        "create_company(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse",
        "            body = CompanyCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.companies.basic_api.create(simple_public_object_input_for_create=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created company')",
        "Create a new company with the given properties.",
    ),
    (
        "update_company(self, company_id: str, properties: dict[str, str]) -> HubSpotResponse",
        "            body = CompanyUpdateInput(properties=properties)  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.companies.basic_api.update(company_id=company_id, simple_public_object_input=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully updated company')",
        "Update an existing company's properties.",
    ),
    (
        "search_companies(self, filter_groups: Optional[list[dict[str, Any]]] = None, query: Optional[str] = None, properties: Optional[list[str]] = None, sorts: Optional[list[dict[str, Any]]] = None, limit: int = 10, after: int = 0) -> HubSpotResponse",
        "            request_body: dict[str, Any] = {'limit': limit, 'after': after}\n"
        "            if filter_groups is not None:\n"
        "                request_body['filter_groups'] = filter_groups\n"
        "            if query is not None:\n"
        "                request_body['query'] = query\n"
        "            if properties is not None:\n"
        "                request_body['properties'] = properties\n"
        "            if sorts is not None:\n"
        "                request_body['sorts'] = sorts\n"
        "            search_request = CompanySearchRequest(**request_body)  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.companies.search_api.do_search(public_object_search_request=search_request)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully searched companies')",
        "Search companies using filter groups, query string, and sorting.",
    ),
]

# ========== DEALS ==========
METHODS += [
    (
        "list_deals(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}\n"
        "            if after is not None:\n"
        "                kwargs['after'] = after\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.deals.basic_api.get_page(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed deals')",
        "List deals with pagination and optional property selection.",
    ),
    (
        "get_deal(self, deal_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'deal_id': deal_id, 'archived': archived}\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.deals.basic_api.get_by_id(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved deal')",
        "Get a single deal by ID.",
    ),
    (
        "create_deal(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse",
        "            body = DealCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.deals.basic_api.create(simple_public_object_input_for_create=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created deal')",
        "Create a new deal with the given properties.",
    ),
    (
        "update_deal(self, deal_id: str, properties: dict[str, str]) -> HubSpotResponse",
        "            body = DealUpdateInput(properties=properties)  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.deals.basic_api.update(deal_id=deal_id, simple_public_object_input=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully updated deal')",
        "Update an existing deal's properties.",
    ),
    (
        "search_deals(self, filter_groups: Optional[list[dict[str, Any]]] = None, query: Optional[str] = None, properties: Optional[list[str]] = None, sorts: Optional[list[dict[str, Any]]] = None, limit: int = 10, after: int = 0) -> HubSpotResponse",
        "            request_body: dict[str, Any] = {'limit': limit, 'after': after}\n"
        "            if filter_groups is not None:\n"
        "                request_body['filter_groups'] = filter_groups\n"
        "            if query is not None:\n"
        "                request_body['query'] = query\n"
        "            if properties is not None:\n"
        "                request_body['properties'] = properties\n"
        "            if sorts is not None:\n"
        "                request_body['sorts'] = sorts\n"
        "            search_request = DealSearchRequest(**request_body)  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.deals.search_api.do_search(public_object_search_request=search_request)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully searched deals')",
        "Search deals using filter groups, query string, and sorting.",
    ),
]

# ========== TICKETS ==========
METHODS += [
    (
        "list_tickets(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}\n"
        "            if after is not None:\n"
        "                kwargs['after'] = after\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.tickets.basic_api.get_page(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed tickets')",
        "List tickets with pagination and optional property selection.",
    ),
    (
        "get_ticket(self, ticket_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'ticket_id': ticket_id, 'archived': archived}\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.tickets.basic_api.get_by_id(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved ticket')",
        "Get a single ticket by ID.",
    ),
    (
        "create_ticket(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse",
        "            body = TicketCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.tickets.basic_api.create(simple_public_object_input_for_create=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created ticket')",
        "Create a new ticket with the given properties.",
    ),
]

# ========== NOTES / ENGAGEMENTS ==========
METHODS += [
    (
        "list_notes(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}\n"
        "            if after is not None:\n"
        "                kwargs['after'] = after\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.objects.notes.basic_api.get_page(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed notes')",
        "List notes/engagements with pagination.",
    ),
    (
        "get_note(self, note_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'note_id': note_id, 'archived': archived}\n"
        "            if properties is not None:\n"
        "                kwargs['properties'] = properties\n"
        "            result = self._sdk.crm.objects.notes.basic_api.get_by_id(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved note')",
        "Get a single note/engagement by ID.",
    ),
    (
        "create_note(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse",
        "            body = NoteCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]\n"
        "            result = self._sdk.crm.objects.notes.basic_api.create(simple_public_object_input_for_create=body)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created note')",
        "Create a new note/engagement with the given properties.",
    ),
]

# ========== PIPELINES ==========
METHODS += [
    (
        "list_pipelines(self, object_type: str) -> HubSpotResponse",
        "            result = self._sdk.crm.pipelines.pipelines_api.get_all(object_type=object_type)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed pipelines')",
        "List all pipelines for an object type (e.g. 'deals', 'tickets').",
    ),
    (
        "get_pipeline(self, object_type: str, pipeline_id: str) -> HubSpotResponse",
        "            result = self._sdk.crm.pipelines.pipelines_api.get_by_id(object_type=object_type, pipeline_id=pipeline_id)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved pipeline')",
        "Get a specific pipeline by ID for an object type.",
    ),
]

# ========== PROPERTIES ==========
METHODS += [
    (
        "list_properties(self, object_type: str, archived: bool = False) -> HubSpotResponse",
        "            result = self._sdk.crm.properties.core_api.get_all(object_type=object_type, archived=archived)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed properties')",
        "List all properties for an object type (e.g. 'contacts', 'companies').",
    ),
]

# ========== OWNERS ==========
METHODS += [
    (
        "list_owners(self, limit: int = 100, after: Optional[str] = None, archived: bool = False) -> HubSpotResponse",
        "            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}\n"
        "            if after is not None:\n"
        "                kwargs['after'] = after\n"
        "            result = self._sdk.crm.owners.owners_api.get_page(**kwargs)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed owners')",
        "List all owners (users who can be assigned to CRM records).",
    ),
    (
        "get_owner(self, owner_id: str, archived: bool = False) -> HubSpotResponse",
        "            result = self._sdk.crm.owners.owners_api.get_by_id(owner_id=int(owner_id), archived=archived)\n"
        "            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved owner')",
        "Get a specific owner by ID.",
    ),
]


# ---------------------------------------------------------------------------
# Code emission utilities
# ---------------------------------------------------------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    """Emit a single method with try/except wrapping."""
    method_name = sig.split("(")[0]
    # Body strings already have 12-space indentation (method body inside try).
    # dedent strips it, then we re-indent to 12 spaces (inside try block).
    normalized_body = textwrap.indent(textwrap.dedent(body), "            ")
    lines = [
        f"    def {sig}:",
        f'        """{doc}"""',
        "        try:",
        f"{normalized_body}",
        "        except Exception as e:",
        f"            return HubSpotResponse(success=False, error=str(e), message='Failed to execute {method_name}')",
        "",
    ]
    return "\n".join(lines)


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT,
    class_name: str = DEFAULT_CLASS_NAME,
) -> str:
    """Build the complete class source code."""
    parts: list[str] = []
    header = HEADER.replace("{response_import}", response_import).replace(
        "{class_name}", class_name
    )
    parts.append(header)
    for sig, body, doc in METHODS:
        parts.append(_emit_method(sig, body, doc))
    parts.append(FOOTER)
    return "".join(parts)


def write_output(path: str, code: str) -> None:
    """Write the generated code to the target file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate HubSpotDataSource (hubspot-api-client SDK)."
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output path for the generated data source.",
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line for HubSpotClient and HubSpotResponse.",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS_NAME,
        help="Name of the generated datasource class.",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Also print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class(
        response_import=args.response_import, class_name=args.class_name
    )
    write_output(args.out, code)

    print(f"Generated HubSpotDataSource with {len(METHODS)} methods")
    print(f"Saved to: {args.out}")

    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
