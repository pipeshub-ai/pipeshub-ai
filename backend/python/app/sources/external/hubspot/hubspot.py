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

from app.sources.client.hubspot.hubspot import HubSpotResponse


def _to_dict(obj: object) -> Any:
    """Convert an SDK response object to a plain dict/list."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()  # type: ignore[reportUnknownMemberType]
    return obj


class HubSpotDataSource:
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

    def get_data_source(self) -> "HubSpotDataSource":
        """Return the data source instance."""
        return self

    def list_contacts(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """List contacts with pagination and optional property selection."""
        try:
            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}
            if after is not None:
                kwargs['after'] = after
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.contacts.basic_api.get_page(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed contacts')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_contacts')
    def get_contact(self, contact_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """Get a single contact by ID."""
        try:
            kwargs: dict[str, Any] = {'contact_id': contact_id, 'archived': archived}
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.contacts.basic_api.get_by_id(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved contact')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute get_contact')
    def create_contact(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse:
        """Create a new contact with the given properties."""
        try:
            body = ContactCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.contacts.basic_api.create(simple_public_object_input_for_create=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created contact')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute create_contact')
    def update_contact(self, contact_id: str, properties: dict[str, str]) -> HubSpotResponse:
        """Update an existing contact's properties."""
        try:
            body = ContactUpdateInput(properties=properties)  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.contacts.basic_api.update(contact_id=contact_id, simple_public_object_input=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully updated contact')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute update_contact')
    def delete_contact(self, contact_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a contact by ID."""
        try:
            self._sdk.crm.contacts.basic_api.archive(contact_id=contact_id)
            return HubSpotResponse(success=True, data={'archived': True}, message='Successfully archived contact')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute delete_contact')
    def search_contacts(self, filter_groups: Optional[list[dict[str, Any]]] = None, query: Optional[str] = None, properties: Optional[list[str]] = None, sorts: Optional[list[dict[str, Any]]] = None, limit: int = 10, after: int = 0) -> HubSpotResponse:
        """Search contacts using filter groups, query string, and sorting."""
        try:
            request_body: dict[str, Any] = {'limit': limit, 'after': after}
            if filter_groups is not None:
                request_body['filter_groups'] = filter_groups
            if query is not None:
                request_body['query'] = query
            if properties is not None:
                request_body['properties'] = properties
            if sorts is not None:
                request_body['sorts'] = sorts
            search_request = ContactSearchRequest(**request_body)  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.contacts.search_api.do_search(public_object_search_request=search_request)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully searched contacts')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute search_contacts')
    def list_companies(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """List companies with pagination and optional property selection."""
        try:
            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}
            if after is not None:
                kwargs['after'] = after
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.companies.basic_api.get_page(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed companies')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_companies')
    def get_company(self, company_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """Get a single company by ID."""
        try:
            kwargs: dict[str, Any] = {'company_id': company_id, 'archived': archived}
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.companies.basic_api.get_by_id(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved company')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute get_company')
    def create_company(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse:
        """Create a new company with the given properties."""
        try:
            body = CompanyCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.companies.basic_api.create(simple_public_object_input_for_create=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created company')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute create_company')
    def update_company(self, company_id: str, properties: dict[str, str]) -> HubSpotResponse:
        """Update an existing company's properties."""
        try:
            body = CompanyUpdateInput(properties=properties)  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.companies.basic_api.update(company_id=company_id, simple_public_object_input=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully updated company')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute update_company')
    def search_companies(self, filter_groups: Optional[list[dict[str, Any]]] = None, query: Optional[str] = None, properties: Optional[list[str]] = None, sorts: Optional[list[dict[str, Any]]] = None, limit: int = 10, after: int = 0) -> HubSpotResponse:
        """Search companies using filter groups, query string, and sorting."""
        try:
            request_body: dict[str, Any] = {'limit': limit, 'after': after}
            if filter_groups is not None:
                request_body['filter_groups'] = filter_groups
            if query is not None:
                request_body['query'] = query
            if properties is not None:
                request_body['properties'] = properties
            if sorts is not None:
                request_body['sorts'] = sorts
            search_request = CompanySearchRequest(**request_body)  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.companies.search_api.do_search(public_object_search_request=search_request)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully searched companies')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute search_companies')
    def list_deals(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """List deals with pagination and optional property selection."""
        try:
            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}
            if after is not None:
                kwargs['after'] = after
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.deals.basic_api.get_page(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed deals')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_deals')
    def get_deal(self, deal_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """Get a single deal by ID."""
        try:
            kwargs: dict[str, Any] = {'deal_id': deal_id, 'archived': archived}
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.deals.basic_api.get_by_id(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved deal')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute get_deal')
    def create_deal(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse:
        """Create a new deal with the given properties."""
        try:
            body = DealCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.deals.basic_api.create(simple_public_object_input_for_create=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created deal')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute create_deal')
    def update_deal(self, deal_id: str, properties: dict[str, str]) -> HubSpotResponse:
        """Update an existing deal's properties."""
        try:
            body = DealUpdateInput(properties=properties)  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.deals.basic_api.update(deal_id=deal_id, simple_public_object_input=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully updated deal')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute update_deal')
    def search_deals(self, filter_groups: Optional[list[dict[str, Any]]] = None, query: Optional[str] = None, properties: Optional[list[str]] = None, sorts: Optional[list[dict[str, Any]]] = None, limit: int = 10, after: int = 0) -> HubSpotResponse:
        """Search deals using filter groups, query string, and sorting."""
        try:
            request_body: dict[str, Any] = {'limit': limit, 'after': after}
            if filter_groups is not None:
                request_body['filter_groups'] = filter_groups
            if query is not None:
                request_body['query'] = query
            if properties is not None:
                request_body['properties'] = properties
            if sorts is not None:
                request_body['sorts'] = sorts
            search_request = DealSearchRequest(**request_body)  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.deals.search_api.do_search(public_object_search_request=search_request)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully searched deals')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute search_deals')
    def list_tickets(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """List tickets with pagination and optional property selection."""
        try:
            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}
            if after is not None:
                kwargs['after'] = after
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.tickets.basic_api.get_page(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed tickets')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_tickets')
    def get_ticket(self, ticket_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """Get a single ticket by ID."""
        try:
            kwargs: dict[str, Any] = {'ticket_id': ticket_id, 'archived': archived}
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.tickets.basic_api.get_by_id(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved ticket')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute get_ticket')
    def create_ticket(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse:
        """Create a new ticket with the given properties."""
        try:
            body = TicketCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.tickets.basic_api.create(simple_public_object_input_for_create=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created ticket')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute create_ticket')
    def list_notes(self, limit: int = 10, after: Optional[str] = None, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """List notes/engagements with pagination."""
        try:
            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}
            if after is not None:
                kwargs['after'] = after
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.objects.notes.basic_api.get_page(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed notes')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_notes')
    def get_note(self, note_id: str, properties: Optional[list[str]] = None, archived: bool = False) -> HubSpotResponse:
        """Get a single note/engagement by ID."""
        try:
            kwargs: dict[str, Any] = {'note_id': note_id, 'archived': archived}
            if properties is not None:
                kwargs['properties'] = properties
            result = self._sdk.crm.objects.notes.basic_api.get_by_id(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved note')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute get_note')
    def create_note(self, properties: dict[str, str], associations: Optional[list[dict[str, Any]]] = None) -> HubSpotResponse:
        """Create a new note/engagement with the given properties."""
        try:
            body = NoteCreateInput(properties=properties, associations=associations or [])  # type: ignore[reportUnknownVariableType]
            result = self._sdk.crm.objects.notes.basic_api.create(simple_public_object_input_for_create=body)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully created note')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute create_note')
    def list_pipelines(self, object_type: str) -> HubSpotResponse:
        """List all pipelines for an object type (e.g. 'deals', 'tickets')."""
        try:
            result = self._sdk.crm.pipelines.pipelines_api.get_all(object_type=object_type)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed pipelines')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_pipelines')
    def get_pipeline(self, object_type: str, pipeline_id: str) -> HubSpotResponse:
        """Get a specific pipeline by ID for an object type."""
        try:
            result = self._sdk.crm.pipelines.pipelines_api.get_by_id(object_type=object_type, pipeline_id=pipeline_id)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved pipeline')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute get_pipeline')
    def list_properties(self, object_type: str, archived: bool = False) -> HubSpotResponse:
        """List all properties for an object type (e.g. 'contacts', 'companies')."""
        try:
            result = self._sdk.crm.properties.core_api.get_all(object_type=object_type, archived=archived)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed properties')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_properties')
    def list_owners(self, limit: int = 100, after: Optional[str] = None, archived: bool = False) -> HubSpotResponse:
        """List all owners (users who can be assigned to CRM records)."""
        try:
            kwargs: dict[str, Any] = {'limit': limit, 'archived': archived}
            if after is not None:
                kwargs['after'] = after
            result = self._sdk.crm.owners.owners_api.get_page(**kwargs)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully listed owners')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute list_owners')
    def get_owner(self, owner_id: str, archived: bool = False) -> HubSpotResponse:
        """Get a specific owner by ID."""
        try:
            result = self._sdk.crm.owners.owners_api.get_by_id(owner_id=int(owner_id), archived=archived)
            return HubSpotResponse(success=True, data=_to_dict(result), message='Successfully retrieved owner')
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message='Failed to execute get_owner')
