# ruff: noqa
"""
HubSpot DataSource - Exhaustive SDK wrapper

Auto-generated wrapper around the ``hubspot-api-client`` Python SDK.
All methods call the SDK directly and wrap results in ``HubSpotResponse``.

Covers:
  - CRM: Contacts, Companies, Deals, Tickets, Line Items, Products, Quotes
  - CRM: Notes (Objects), Owners, Pipelines, Properties, Associations, Schemas
  - CRM: Lists, Imports, Exports, Timeline
  - CMS: Domains, HubDB, URL Redirects, Audit Logs
  - Marketing: Forms, Emails, Transactional
  - Settings: Users, Roles, Teams
  - Events: Custom behavioural events
"""
from __future__ import annotations

from typing import Any

from app.sources.client.hubspot.hubspot import HubSpotClient, HubSpotResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_dict(obj: object) -> Any:
    """Convert an SDK response object to a plain dict/list.

    Args:
        obj: Any object returned by the HubSpot SDK.

    Returns:
        A plain dict/list if the object has a ``to_dict`` method, otherwise
        the object itself.  Returns ``None`` when *obj* is ``None``.
    """
    if obj is None:
        return None
    if hasattr(obj, "to_dict"):
        return obj.to_dict()  # type: ignore[reportUnknownMemberType]
    return obj


def _handle_error(e: Exception, method_name: str) -> HubSpotResponse:
    """Build a failed ``HubSpotResponse`` from an exception.

    Args:
        e: The caught exception.
        method_name: Name of the method that failed (for the message).

    Returns:
        HubSpotResponse with ``success=False``.
    """
    return HubSpotResponse(
        success=False,
        error=str(e),
        message=f"Failed to execute {method_name}",
    )


# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------


class HubSpotDataSource:
    """Exhaustive HubSpot SDK DataSource.

    Typed wrapper around the official ``hubspot-api-client`` SDK covering
    CRM, CMS, Marketing, Settings and Events API groups.

    Accepts a ``HubSpotClient`` (which exposes ``.get_sdk() -> HubSpot``) or
    a raw ``HubSpot`` SDK instance.  All methods are **synchronous** (the
    underlying SDK is synchronous) and return ``HubSpotResponse`` objects.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, client_or_sdk: HubSpotClient | object) -> None:
        """Initialise with a HubSpotClient or raw HubSpot SDK instance.

        Args:
            client_or_sdk: A ``HubSpotClient`` with ``.get_sdk()`` or a raw
                ``HubSpot`` instance directly.
        """
        if hasattr(client_or_sdk, "get_sdk"):
            self._sdk: Any = client_or_sdk.get_sdk()  # type: ignore[reportUnknownMemberType]
            self._client: Any = client_or_sdk
        else:
            self._sdk = client_or_sdk
            self._client = None

    def get_data_source(self) -> HubSpotDataSource:
        """Return the data-source instance itself."""
        return self

    def get_client(self) -> Any:
        """Return the underlying ``HubSpotClient`` if one was provided."""
        return self._client

    def get_sdk(self) -> Any:
        """Return the raw ``HubSpot`` SDK instance."""
        return self._sdk

    # =====================================================================
    # CRM - Contacts
    # =====================================================================

    def list_contacts(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM contacts with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include in the response.
            archived: Whether to include only archived contacts.

        Returns:
            HubSpotResponse with paginated contacts data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.contacts.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed contacts",
            )
        except Exception as e:
            return _handle_error(e, "list_contacts")

    def get_contact(
        self,
        contact_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM contact by ID.

        Args:
            contact_id: The contact record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived contact.

        Returns:
            HubSpotResponse with the contact data.
        """
        try:
            kwargs: dict[str, Any] = {"contact_id": contact_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.contacts.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved contact",
            )
        except Exception as e:
            return _handle_error(e, "get_contact")

    def create_contact(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM contact.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created contact data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created contact",
            )
        except Exception as e:
            return _handle_error(e, "create_contact")

    def update_contact(
        self,
        contact_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM contact.

        Args:
            contact_id: The contact record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated contact data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.contacts.basic_api.update(
                contact_id=contact_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated contact",
            )
        except Exception as e:
            return _handle_error(e, "update_contact")

    def archive_contact(self, contact_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM contact.

        Args:
            contact_id: The contact record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.contacts.basic_api.archive(contact_id=contact_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived contact",
            )
        except Exception as e:
            return _handle_error(e, "archive_contact")

    def merge_contacts(self, merge_input: dict[str, Any]) -> HubSpotResponse:
        """Merge two contacts.

        Args:
            merge_input: Dict with ``objectIdToMerge`` and ``resultingObjectId``.

        Returns:
            HubSpotResponse with the merged contact data.
        """
        try:
            result = self._sdk.crm.contacts.basic_api.merge(
                public_merge_input=merge_input  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully merged contacts",
            )
        except Exception as e:
            return _handle_error(e, "merge_contacts")

    def batch_create_contacts(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create contacts.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created contacts.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.contacts.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created contacts",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_contacts")

    def batch_read_contacts(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read contacts by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested contacts.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.contacts.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read contacts",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_contacts")

    def batch_update_contacts(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update contacts.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated contacts.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.contacts.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated contacts",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_contacts")

    def batch_archive_contacts(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive contacts.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.contacts.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived contacts",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_contacts")

    def search_contacts(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM contacts using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching contacts.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.contacts.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched contacts",
            )
        except Exception as e:
            return _handle_error(e, "search_contacts")

    # =====================================================================
    # CRM - Companies
    # =====================================================================

    def list_companies(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM companies with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include.
            archived: Whether to include only archived companies.

        Returns:
            HubSpotResponse with paginated companies data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.companies.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed companies",
            )
        except Exception as e:
            return _handle_error(e, "list_companies")

    def get_company(
        self,
        company_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM company by ID.

        Args:
            company_id: The company record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived company.

        Returns:
            HubSpotResponse with the company data.
        """
        try:
            kwargs: dict[str, Any] = {"company_id": company_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.companies.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved company",
            )
        except Exception as e:
            return _handle_error(e, "get_company")

    def create_company(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM company.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created company data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.companies.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created company",
            )
        except Exception as e:
            return _handle_error(e, "create_company")

    def update_company(
        self,
        company_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM company.

        Args:
            company_id: The company record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated company data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.companies.basic_api.update(
                company_id=company_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated company",
            )
        except Exception as e:
            return _handle_error(e, "update_company")

    def archive_company(self, company_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM company.

        Args:
            company_id: The company record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.companies.basic_api.archive(company_id=company_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived company",
            )
        except Exception as e:
            return _handle_error(e, "archive_company")

    def merge_companies(self, merge_input: dict[str, Any]) -> HubSpotResponse:
        """Merge two companies.

        Args:
            merge_input: Dict with ``objectIdToMerge`` and ``resultingObjectId``.

        Returns:
            HubSpotResponse with the merged company data.
        """
        try:
            result = self._sdk.crm.companies.basic_api.merge(
                public_merge_input=merge_input  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully merged companies",
            )
        except Exception as e:
            return _handle_error(e, "merge_companies")

    def batch_create_companies(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create companies.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created companies.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.companies.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created companies",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_companies")

    def batch_read_companies(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read companies by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested companies.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.companies.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read companies",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_companies")

    def batch_update_companies(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update companies.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated companies.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.companies.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated companies",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_companies")

    def batch_archive_companies(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive companies.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.companies.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived companies",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_companies")

    def search_companies(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM companies using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching companies.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.companies.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched companies",
            )
        except Exception as e:
            return _handle_error(e, "search_companies")

    # =====================================================================
    # CRM - Deals
    # =====================================================================

    def list_deals(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM deals with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include.
            archived: Whether to include only archived deals.

        Returns:
            HubSpotResponse with paginated deals data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.deals.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed deals",
            )
        except Exception as e:
            return _handle_error(e, "list_deals")

    def get_deal(
        self,
        deal_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM deal by ID.

        Args:
            deal_id: The deal record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived deal.

        Returns:
            HubSpotResponse with the deal data.
        """
        try:
            kwargs: dict[str, Any] = {"deal_id": deal_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.deals.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved deal",
            )
        except Exception as e:
            return _handle_error(e, "get_deal")

    def create_deal(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM deal.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created deal data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.deals.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created deal",
            )
        except Exception as e:
            return _handle_error(e, "create_deal")

    def update_deal(
        self,
        deal_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM deal.

        Args:
            deal_id: The deal record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated deal data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated deal",
            )
        except Exception as e:
            return _handle_error(e, "update_deal")

    def archive_deal(self, deal_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM deal.

        Args:
            deal_id: The deal record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.deals.basic_api.archive(deal_id=deal_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived deal",
            )
        except Exception as e:
            return _handle_error(e, "archive_deal")

    def merge_deals(self, merge_input: dict[str, Any]) -> HubSpotResponse:
        """Merge two deals.

        Args:
            merge_input: Dict with ``objectIdToMerge`` and ``resultingObjectId``.

        Returns:
            HubSpotResponse with the merged deal data.
        """
        try:
            result = self._sdk.crm.deals.basic_api.merge(
                public_merge_input=merge_input  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully merged deals",
            )
        except Exception as e:
            return _handle_error(e, "merge_deals")

    def batch_create_deals(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create deals.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created deals.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.deals.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created deals",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_deals")

    def batch_read_deals(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read deals by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested deals.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.deals.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read deals",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_deals")

    def batch_update_deals(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update deals.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated deals.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.deals.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated deals",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_deals")

    def batch_archive_deals(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive deals.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.deals.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived deals",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_deals")

    def search_deals(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM deals using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching deals.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.deals.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched deals",
            )
        except Exception as e:
            return _handle_error(e, "search_deals")

    # =====================================================================
    # CRM - Tickets
    # =====================================================================

    def list_tickets(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM tickets with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include.
            archived: Whether to include only archived tickets.

        Returns:
            HubSpotResponse with paginated tickets data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.tickets.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed tickets",
            )
        except Exception as e:
            return _handle_error(e, "list_tickets")

    def get_ticket(
        self,
        ticket_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM ticket by ID.

        Args:
            ticket_id: The ticket record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived ticket.

        Returns:
            HubSpotResponse with the ticket data.
        """
        try:
            kwargs: dict[str, Any] = {"ticket_id": ticket_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.tickets.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved ticket",
            )
        except Exception as e:
            return _handle_error(e, "get_ticket")

    def create_ticket(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM ticket.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created ticket data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.tickets.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created ticket",
            )
        except Exception as e:
            return _handle_error(e, "create_ticket")

    def update_ticket(
        self,
        ticket_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM ticket.

        Args:
            ticket_id: The ticket record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated ticket data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.tickets.basic_api.update(
                ticket_id=ticket_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated ticket",
            )
        except Exception as e:
            return _handle_error(e, "update_ticket")

    def archive_ticket(self, ticket_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM ticket.

        Args:
            ticket_id: The ticket record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.tickets.basic_api.archive(ticket_id=ticket_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived ticket",
            )
        except Exception as e:
            return _handle_error(e, "archive_ticket")

    def merge_tickets(self, merge_input: dict[str, Any]) -> HubSpotResponse:
        """Merge two tickets.

        Args:
            merge_input: Dict with ``objectIdToMerge`` and ``resultingObjectId``.

        Returns:
            HubSpotResponse with the merged ticket data.
        """
        try:
            result = self._sdk.crm.tickets.basic_api.merge(
                public_merge_input=merge_input  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully merged tickets",
            )
        except Exception as e:
            return _handle_error(e, "merge_tickets")

    def batch_create_tickets(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create tickets.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created tickets.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.tickets.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created tickets",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_tickets")

    def batch_read_tickets(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read tickets by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested tickets.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.tickets.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read tickets",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_tickets")

    def batch_update_tickets(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update tickets.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated tickets.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.tickets.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated tickets",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_tickets")

    def batch_archive_tickets(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive tickets.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.tickets.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived tickets",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_tickets")

    def search_tickets(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM tickets using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching tickets.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.tickets.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched tickets",
            )
        except Exception as e:
            return _handle_error(e, "search_tickets")

    # =====================================================================
    # CRM - Line Items
    # =====================================================================

    def list_line_items(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM line items with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include.
            archived: Whether to include only archived line items.

        Returns:
            HubSpotResponse with paginated line items data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.line_items.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed line items",
            )
        except Exception as e:
            return _handle_error(e, "list_line_items")

    def get_line_item(
        self,
        line_item_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM line item by ID.

        Args:
            line_item_id: The line item record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived line item.

        Returns:
            HubSpotResponse with the line item data.
        """
        try:
            kwargs: dict[str, Any] = {"line_item_id": line_item_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.line_items.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved line item",
            )
        except Exception as e:
            return _handle_error(e, "get_line_item")

    def create_line_item(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM line item.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created line item data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.line_items.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created line item",
            )
        except Exception as e:
            return _handle_error(e, "create_line_item")

    def update_line_item(
        self,
        line_item_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM line item.

        Args:
            line_item_id: The line item record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated line item data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.line_items.basic_api.update(
                line_item_id=line_item_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated line item",
            )
        except Exception as e:
            return _handle_error(e, "update_line_item")

    def archive_line_item(self, line_item_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM line item.

        Args:
            line_item_id: The line item record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.line_items.basic_api.archive(line_item_id=line_item_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived line item",
            )
        except Exception as e:
            return _handle_error(e, "archive_line_item")

    def batch_create_line_items(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create line items.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created line items.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.line_items.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created line items",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_line_items")

    def batch_read_line_items(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read line items by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested line items.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.line_items.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read line items",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_line_items")

    def batch_update_line_items(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update line items.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated line items.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.line_items.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated line items",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_line_items")

    def batch_archive_line_items(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive line items.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.line_items.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived line items",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_line_items")

    def search_line_items(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM line items using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching line items.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.line_items.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched line items",
            )
        except Exception as e:
            return _handle_error(e, "search_line_items")

    # =====================================================================
    # CRM - Products
    # =====================================================================

    def list_products(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM products with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include.
            archived: Whether to include only archived products.

        Returns:
            HubSpotResponse with paginated products data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.products.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed products",
            )
        except Exception as e:
            return _handle_error(e, "list_products")

    def get_product(
        self,
        product_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM product by ID.

        Args:
            product_id: The product record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived product.

        Returns:
            HubSpotResponse with the product data.
        """
        try:
            kwargs: dict[str, Any] = {"product_id": product_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.products.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved product",
            )
        except Exception as e:
            return _handle_error(e, "get_product")

    def create_product(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM product.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created product data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.products.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created product",
            )
        except Exception as e:
            return _handle_error(e, "create_product")

    def update_product(
        self,
        product_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM product.

        Args:
            product_id: The product record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated product data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.products.basic_api.update(
                product_id=product_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated product",
            )
        except Exception as e:
            return _handle_error(e, "update_product")

    def archive_product(self, product_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM product.

        Args:
            product_id: The product record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.products.basic_api.archive(product_id=product_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived product",
            )
        except Exception as e:
            return _handle_error(e, "archive_product")

    def batch_create_products(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create products.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created products.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.products.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created products",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_products")

    def batch_read_products(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read products by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested products.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.products.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read products",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_products")

    def batch_update_products(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update products.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated products.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.products.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated products",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_products")

    def batch_archive_products(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive products.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.products.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived products",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_products")

    def search_products(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM products using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching products.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.products.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched products",
            )
        except Exception as e:
            return _handle_error(e, "search_products")

    # =====================================================================
    # CRM - Quotes
    # =====================================================================

    def list_quotes(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM quotes with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include.
            archived: Whether to include only archived quotes.

        Returns:
            HubSpotResponse with paginated quotes data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.quotes.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed quotes",
            )
        except Exception as e:
            return _handle_error(e, "list_quotes")

    def get_quote(
        self,
        quote_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM quote by ID.

        Args:
            quote_id: The quote record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived quote.

        Returns:
            HubSpotResponse with the quote data.
        """
        try:
            kwargs: dict[str, Any] = {"quote_id": quote_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.quotes.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved quote",
            )
        except Exception as e:
            return _handle_error(e, "get_quote")

    def create_quote(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM quote.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created quote data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.quotes.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created quote",
            )
        except Exception as e:
            return _handle_error(e, "create_quote")

    def update_quote(
        self,
        quote_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM quote.

        Args:
            quote_id: The quote record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated quote data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.quotes.basic_api.update(
                quote_id=quote_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated quote",
            )
        except Exception as e:
            return _handle_error(e, "update_quote")

    def archive_quote(self, quote_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM quote.

        Args:
            quote_id: The quote record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.quotes.basic_api.archive(quote_id=quote_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived quote",
            )
        except Exception as e:
            return _handle_error(e, "archive_quote")

    def batch_create_quotes(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create quotes.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created quotes.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.quotes.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created quotes",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_quotes")

    def batch_read_quotes(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read quotes by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested quotes.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.quotes.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read quotes",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_quotes")

    def batch_update_quotes(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update quotes.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated quotes.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.quotes.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated quotes",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_quotes")

    def batch_archive_quotes(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive quotes.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.quotes.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived quotes",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_quotes")

    def search_quotes(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM quotes using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching quotes.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.quotes.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched quotes",
            )
        except Exception as e:
            return _handle_error(e, "search_quotes")

    # =====================================================================
    # CRM - Objects / Notes
    # =====================================================================

    def list_notes(
        self,
        limit: int = 10,
        after: str | None = None,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM notes (engagements) with pagination.

        Args:
            limit: Max number of results per page (default 10, max 100).
            after: Cursor token for the next page.
            properties: List of property names to include.
            archived: Whether to include only archived notes.

        Returns:
            HubSpotResponse with paginated notes data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.objects.notes.basic_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed notes",
            )
        except Exception as e:
            return _handle_error(e, "list_notes")

    def get_note(
        self,
        note_id: str,
        properties: list[str] | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single CRM note by ID.

        Args:
            note_id: The note record ID.
            properties: List of property names to include.
            archived: Whether to retrieve an archived note.

        Returns:
            HubSpotResponse with the note data.
        """
        try:
            kwargs: dict[str, Any] = {"note_id": note_id, "archived": archived}
            if properties is not None:
                kwargs["properties"] = properties
            result = self._sdk.crm.objects.notes.basic_api.get_by_id(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved note",
            )
        except Exception as e:
            return _handle_error(e, "get_note")

    def create_note(
        self,
        properties: dict[str, str],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new CRM note.

        Args:
            properties: A mapping of property name to value.
            associations: Optional list of association definitions.

        Returns:
            HubSpotResponse with the created note data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            if associations is not None:
                body["associations"] = associations
            result = self._sdk.crm.objects.notes.basic_api.create(
                simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created note",
            )
        except Exception as e:
            return _handle_error(e, "create_note")

    def update_note(
        self,
        note_id: str,
        properties: dict[str, str],
    ) -> HubSpotResponse:
        """Update an existing CRM note.

        Args:
            note_id: The note record ID.
            properties: A mapping of property name to new value.

        Returns:
            HubSpotResponse with the updated note data.
        """
        try:
            body: dict[str, Any] = {"properties": properties}
            result = self._sdk.crm.objects.notes.basic_api.update(
                note_id=note_id,
                simple_public_object_input=body,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated note",
            )
        except Exception as e:
            return _handle_error(e, "update_note")

    def archive_note(self, note_id: str) -> HubSpotResponse:
        """Archive (soft-delete) a CRM note.

        Args:
            note_id: The note record ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.objects.notes.basic_api.archive(note_id=note_id)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived note",
            )
        except Exception as e:
            return _handle_error(e, "archive_note")

    def batch_create_notes(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-create notes.

        Args:
            inputs: List of dicts each containing ``properties`` (and optional
                ``associations``).

        Returns:
            HubSpotResponse with the created notes.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.objects.notes.batch_api.create(
                batch_input_simple_public_object_input_for_create=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-created notes",
            )
        except Exception as e:
            return _handle_error(e, "batch_create_notes")

    def batch_read_notes(
        self, inputs: list[dict[str, Any]], properties: list[str] | None = None
    ) -> HubSpotResponse:
        """Batch-read notes by ID.

        Args:
            inputs: List of dicts each containing an ``id`` key.
            properties: Optional list of property names to include.

        Returns:
            HubSpotResponse with the requested notes.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            if properties is not None:
                body["properties"] = properties
            result = self._sdk.crm.objects.notes.batch_api.read(
                batch_read_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-read notes",
            )
        except Exception as e:
            return _handle_error(e, "batch_read_notes")

    def batch_update_notes(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-update notes.

        Args:
            inputs: List of dicts each containing ``id`` and ``properties``.

        Returns:
            HubSpotResponse with the updated notes.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            result = self._sdk.crm.objects.notes.batch_api.update(
                batch_input_simple_public_object_batch_input=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully batch-updated notes",
            )
        except Exception as e:
            return _handle_error(e, "batch_update_notes")

    def batch_archive_notes(
        self, inputs: list[dict[str, Any]]
    ) -> HubSpotResponse:
        """Batch-archive notes.

        Args:
            inputs: List of dicts each containing an ``id`` key.

        Returns:
            HubSpotResponse confirming the batch archive.
        """
        try:
            body: dict[str, Any] = {"inputs": inputs}
            self._sdk.crm.objects.notes.batch_api.archive(
                batch_input_simple_public_object_id=body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully batch-archived notes",
            )
        except Exception as e:
            return _handle_error(e, "batch_archive_notes")

    def search_notes(
        self,
        filter_groups: list[dict[str, Any]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        limit: int = 10,
        after: int = 0,
    ) -> HubSpotResponse:
        """Search CRM notes using filters and/or a text query.

        Args:
            filter_groups: Filter group definitions for the search.
            query: Free-text search query.
            properties: Property names to return.
            sorts: Sort definitions.
            limit: Maximum results per page.
            after: Pagination offset.

        Returns:
            HubSpotResponse with matching notes.
        """
        try:
            request_body: dict[str, Any] = {"limit": limit, "after": after}
            if filter_groups is not None:
                request_body["filter_groups"] = filter_groups
            if query is not None:
                request_body["query"] = query
            if properties is not None:
                request_body["properties"] = properties
            if sorts is not None:
                request_body["sorts"] = sorts
            result = self._sdk.crm.objects.notes.search_api.do_search(
                public_object_search_request=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched notes",
            )
        except Exception as e:
            return _handle_error(e, "search_notes")

    # =====================================================================
    # CRM - Owners
    # =====================================================================

    def list_owners(
        self,
        limit: int = 100,
        after: str | None = None,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List CRM owners (users who can be assigned to records).

        Args:
            limit: Max number of results per page (default 100).
            after: Cursor token for the next page.
            archived: Whether to include only archived owners.

        Returns:
            HubSpotResponse with paginated owners data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit, "archived": archived}
            if after is not None:
                kwargs["after"] = after
            result = self._sdk.crm.owners.owners_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed owners",
            )
        except Exception as e:
            return _handle_error(e, "list_owners")

    def get_owner(
        self,
        owner_id: str,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a specific CRM owner by ID.

        Args:
            owner_id: The owner ID (numeric, will be cast to int).
            archived: Whether to retrieve an archived owner.

        Returns:
            HubSpotResponse with the owner data.
        """
        try:
            result = self._sdk.crm.owners.owners_api.get_by_id(
                owner_id=int(owner_id), archived=archived  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved owner",
            )
        except Exception as e:
            return _handle_error(e, "get_owner")

    # =====================================================================
    # CRM - Pipelines
    # =====================================================================

    def list_pipelines(self, object_type: str) -> HubSpotResponse:
        """List all pipelines for a given object type.

        Args:
            object_type: The CRM object type (e.g. ``"deals"``, ``"tickets"``).

        Returns:
            HubSpotResponse with the list of pipelines.
        """
        try:
            result = self._sdk.crm.pipelines.pipelines_api.get_all(
                object_type=object_type  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed pipelines",
            )
        except Exception as e:
            return _handle_error(e, "list_pipelines")

    def get_pipeline(
        self, object_type: str, pipeline_id: str
    ) -> HubSpotResponse:
        """Get a specific pipeline by ID.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.

        Returns:
            HubSpotResponse with the pipeline data.
        """
        try:
            result = self._sdk.crm.pipelines.pipelines_api.get_by_id(
                object_type=object_type, pipeline_id=pipeline_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved pipeline",
            )
        except Exception as e:
            return _handle_error(e, "get_pipeline")

    def create_pipeline(
        self, object_type: str, pipeline_input: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new pipeline.

        Args:
            object_type: The CRM object type (e.g. ``"deals"``).
            pipeline_input: Dict with pipeline definition (``label``, ``stages``, etc.).

        Returns:
            HubSpotResponse with the created pipeline data.
        """
        try:
            result = self._sdk.crm.pipelines.pipelines_api.create(
                object_type=object_type,
                pipeline_input=pipeline_input,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created pipeline",
            )
        except Exception as e:
            return _handle_error(e, "create_pipeline")

    def update_pipeline(
        self,
        object_type: str,
        pipeline_id: str,
        pipeline_patch_input: dict[str, Any],
    ) -> HubSpotResponse:
        """Update an existing pipeline.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.
            pipeline_patch_input: Dict with fields to update.

        Returns:
            HubSpotResponse with the updated pipeline data.
        """
        try:
            result = self._sdk.crm.pipelines.pipelines_api.update(
                object_type=object_type,
                pipeline_id=pipeline_id,
                pipeline_patch_input=pipeline_patch_input,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated pipeline",
            )
        except Exception as e:
            return _handle_error(e, "update_pipeline")

    def archive_pipeline(
        self, object_type: str, pipeline_id: str
    ) -> HubSpotResponse:
        """Archive a pipeline.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.pipelines.pipelines_api.archive(
                object_type=object_type, pipeline_id=pipeline_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived pipeline",
            )
        except Exception as e:
            return _handle_error(e, "archive_pipeline")

    # ------------------------------------------------------------------
    # Pipeline Stages
    # ------------------------------------------------------------------

    def list_pipeline_stages(
        self, object_type: str, pipeline_id: str
    ) -> HubSpotResponse:
        """List all stages for a pipeline.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.

        Returns:
            HubSpotResponse with the list of stages.
        """
        try:
            result = self._sdk.crm.pipelines.pipeline_stages_api.get_all(
                object_type=object_type, pipeline_id=pipeline_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed pipeline stages",
            )
        except Exception as e:
            return _handle_error(e, "list_pipeline_stages")

    def get_pipeline_stage(
        self, object_type: str, pipeline_id: str, stage_id: str
    ) -> HubSpotResponse:
        """Get a specific pipeline stage by ID.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.
            stage_id: The stage ID.

        Returns:
            HubSpotResponse with the stage data.
        """
        try:
            result = self._sdk.crm.pipelines.pipeline_stages_api.get_by_id(
                object_type=object_type,
                pipeline_id=pipeline_id,
                stage_id=stage_id,  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved pipeline stage",
            )
        except Exception as e:
            return _handle_error(e, "get_pipeline_stage")

    def create_pipeline_stage(
        self,
        object_type: str,
        pipeline_id: str,
        pipeline_stage_input: dict[str, Any],
    ) -> HubSpotResponse:
        """Create a new stage in a pipeline.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.
            pipeline_stage_input: Dict with stage definition (``label``, ``displayOrder``, etc.).

        Returns:
            HubSpotResponse with the created stage data.
        """
        try:
            result = self._sdk.crm.pipelines.pipeline_stages_api.create(
                object_type=object_type,
                pipeline_id=pipeline_id,
                pipeline_stage_input=pipeline_stage_input,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created pipeline stage",
            )
        except Exception as e:
            return _handle_error(e, "create_pipeline_stage")

    def update_pipeline_stage(
        self,
        object_type: str,
        pipeline_id: str,
        stage_id: str,
        pipeline_stage_patch_input: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a pipeline stage.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.
            stage_id: The stage ID.
            pipeline_stage_patch_input: Dict with fields to update.

        Returns:
            HubSpotResponse with the updated stage data.
        """
        try:
            result = self._sdk.crm.pipelines.pipeline_stages_api.update(
                object_type=object_type,
                pipeline_id=pipeline_id,
                stage_id=stage_id,
                pipeline_stage_patch_input=pipeline_stage_patch_input,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated pipeline stage",
            )
        except Exception as e:
            return _handle_error(e, "update_pipeline_stage")

    def archive_pipeline_stage(
        self, object_type: str, pipeline_id: str, stage_id: str
    ) -> HubSpotResponse:
        """Archive a pipeline stage.

        Args:
            object_type: The CRM object type.
            pipeline_id: The pipeline ID.
            stage_id: The stage ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.pipelines.pipeline_stages_api.archive(
                object_type=object_type,
                pipeline_id=pipeline_id,
                stage_id=stage_id,  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived pipeline stage",
            )
        except Exception as e:
            return _handle_error(e, "archive_pipeline_stage")

    # =====================================================================
    # CRM - Properties
    # =====================================================================

    def list_properties(
        self,
        object_type: str,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """List all properties for a CRM object type.

        Args:
            object_type: The CRM object type (e.g. ``"contacts"``, ``"companies"``).
            archived: Whether to include only archived properties.

        Returns:
            HubSpotResponse with the list of properties.
        """
        try:
            result = self._sdk.crm.properties.core_api.get_all(
                object_type=object_type, archived=archived  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed properties",
            )
        except Exception as e:
            return _handle_error(e, "list_properties")

    def get_property(
        self,
        object_type: str,
        property_name: str,
        *,
        archived: bool = False,
    ) -> HubSpotResponse:
        """Get a single property definition by name.

        Args:
            object_type: The CRM object type.
            property_name: The internal property name.
            archived: Whether to retrieve an archived property.

        Returns:
            HubSpotResponse with the property definition.
        """
        try:
            result = self._sdk.crm.properties.core_api.get_by_name(
                object_type=object_type,
                property_name=property_name,
                archived=archived,  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved property",
            )
        except Exception as e:
            return _handle_error(e, "get_property")

    def create_property(
        self,
        object_type: str,
        property_create: dict[str, Any],
    ) -> HubSpotResponse:
        """Create a new property definition.

        Args:
            object_type: The CRM object type.
            property_create: Dict with property definition (``name``, ``label``,
                ``type``, ``fieldType``, ``groupName``, etc.).

        Returns:
            HubSpotResponse with the created property.
        """
        try:
            result = self._sdk.crm.properties.core_api.create(
                object_type=object_type,
                property_create=property_create,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created property",
            )
        except Exception as e:
            return _handle_error(e, "create_property")

    def update_property(
        self,
        object_type: str,
        property_name: str,
        property_update: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a property definition.

        Args:
            object_type: The CRM object type.
            property_name: The internal property name to update.
            property_update: Dict with fields to update (``label``, ``description``, etc.).

        Returns:
            HubSpotResponse with the updated property.
        """
        try:
            result = self._sdk.crm.properties.core_api.update(
                object_type=object_type,
                property_name=property_name,
                property_update=property_update,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated property",
            )
        except Exception as e:
            return _handle_error(e, "update_property")

    def archive_property(
        self, object_type: str, property_name: str
    ) -> HubSpotResponse:
        """Archive a property definition.

        Args:
            object_type: The CRM object type.
            property_name: The internal property name to archive.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.properties.core_api.archive(
                object_type=object_type, property_name=property_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived property",
            )
        except Exception as e:
            return _handle_error(e, "archive_property")

    # ------------------------------------------------------------------
    # Property Groups
    # ------------------------------------------------------------------

    def list_property_groups(self, object_type: str) -> HubSpotResponse:
        """List all property groups for a CRM object type.

        Args:
            object_type: The CRM object type.

        Returns:
            HubSpotResponse with the list of property groups.
        """
        try:
            result = self._sdk.crm.properties.groups_api.get_all(
                object_type=object_type  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed property groups",
            )
        except Exception as e:
            return _handle_error(e, "list_property_groups")

    def get_property_group(
        self, object_type: str, group_name: str
    ) -> HubSpotResponse:
        """Get a specific property group by name.

        Args:
            object_type: The CRM object type.
            group_name: The internal group name.

        Returns:
            HubSpotResponse with the property group definition.
        """
        try:
            result = self._sdk.crm.properties.groups_api.get_by_name(
                object_type=object_type, group_name=group_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved property group",
            )
        except Exception as e:
            return _handle_error(e, "get_property_group")

    def create_property_group(
        self, object_type: str, property_group_create: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new property group.

        Args:
            object_type: The CRM object type.
            property_group_create: Dict with group definition (``name``, ``label``, etc.).

        Returns:
            HubSpotResponse with the created property group.
        """
        try:
            result = self._sdk.crm.properties.groups_api.create(
                object_type=object_type,
                property_group_create=property_group_create,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created property group",
            )
        except Exception as e:
            return _handle_error(e, "create_property_group")

    def update_property_group(
        self,
        object_type: str,
        group_name: str,
        property_group_update: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a property group.

        Args:
            object_type: The CRM object type.
            group_name: The internal group name.
            property_group_update: Dict with fields to update.

        Returns:
            HubSpotResponse with the updated property group.
        """
        try:
            result = self._sdk.crm.properties.groups_api.update(
                object_type=object_type,
                group_name=group_name,
                property_group_update=property_group_update,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated property group",
            )
        except Exception as e:
            return _handle_error(e, "update_property_group")

    def archive_property_group(
        self, object_type: str, group_name: str
    ) -> HubSpotResponse:
        """Archive a property group.

        Args:
            object_type: The CRM object type.
            group_name: The internal group name to archive.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.properties.groups_api.archive(
                object_type=object_type, group_name=group_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived property group",
            )
        except Exception as e:
            return _handle_error(e, "archive_property_group")

    # =====================================================================
    # CRM - Associations
    # =====================================================================

    def create_associations(
        self,
        from_object_type: str,
        to_object_type: str,
        batch_input: dict[str, Any],
    ) -> HubSpotResponse:
        """Batch-create associations between CRM objects.

        Args:
            from_object_type: Source object type (e.g. ``"contacts"``).
            to_object_type: Target object type (e.g. ``"companies"``).
            batch_input: Dict with ``inputs`` list of association definitions.

        Returns:
            HubSpotResponse with the created associations.
        """
        try:
            result = self._sdk.crm.associations.batch_api.create(
                from_object_type=from_object_type,
                to_object_type=to_object_type,
                batch_input_public_association=batch_input,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created associations",
            )
        except Exception as e:
            return _handle_error(e, "create_associations")

    def read_associations(
        self,
        from_object_type: str,
        to_object_type: str,
        batch_input: dict[str, Any],
    ) -> HubSpotResponse:
        """Batch-read associations between CRM objects.

        Args:
            from_object_type: Source object type.
            to_object_type: Target object type.
            batch_input: Dict with ``inputs`` list of object IDs to read.

        Returns:
            HubSpotResponse with the association data.
        """
        try:
            result = self._sdk.crm.associations.batch_api.read(
                from_object_type=from_object_type,
                to_object_type=to_object_type,
                batch_input_public_object_id=batch_input,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully read associations",
            )
        except Exception as e:
            return _handle_error(e, "read_associations")

    def archive_associations(
        self,
        from_object_type: str,
        to_object_type: str,
        batch_input: dict[str, Any],
    ) -> HubSpotResponse:
        """Batch-archive associations between CRM objects.

        Args:
            from_object_type: Source object type.
            to_object_type: Target object type.
            batch_input: Dict with ``inputs`` list of associations to archive.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.associations.batch_api.archive(
                from_object_type=from_object_type,
                to_object_type=to_object_type,
                batch_input_public_association=batch_input,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived associations",
            )
        except Exception as e:
            return _handle_error(e, "archive_associations")

    # =====================================================================
    # CRM - Schemas (Custom Objects)
    # =====================================================================

    def list_schemas(self) -> HubSpotResponse:
        """List all custom object schemas.

        Returns:
            HubSpotResponse with the list of custom object schemas.
        """
        try:
            result = self._sdk.crm.schemas.core_api.get_all()  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed schemas",
            )
        except Exception as e:
            return _handle_error(e, "list_schemas")

    def get_schema(self, object_type: str) -> HubSpotResponse:
        """Get a custom object schema by type.

        Args:
            object_type: The custom object type name or ID.

        Returns:
            HubSpotResponse with the schema definition.
        """
        try:
            result = self._sdk.crm.schemas.core_api.get_by_id(
                object_type=object_type  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved schema",
            )
        except Exception as e:
            return _handle_error(e, "get_schema")

    def create_schema(
        self, object_schema_egg: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new custom object schema.

        Args:
            object_schema_egg: Dict with schema definition (``name``, ``labels``,
                ``properties``, ``associatedObjects``, etc.).

        Returns:
            HubSpotResponse with the created schema.
        """
        try:
            result = self._sdk.crm.schemas.core_api.create(
                object_schema_egg=object_schema_egg  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created schema",
            )
        except Exception as e:
            return _handle_error(e, "create_schema")

    def update_schema(
        self,
        object_type: str,
        object_type_definition_patch: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a custom object schema.

        Args:
            object_type: The custom object type name or ID.
            object_type_definition_patch: Dict with fields to update.

        Returns:
            HubSpotResponse with the updated schema.
        """
        try:
            result = self._sdk.crm.schemas.core_api.update(
                object_type=object_type,
                object_type_definition_patch=object_type_definition_patch,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated schema",
            )
        except Exception as e:
            return _handle_error(e, "update_schema")

    def archive_schema(self, object_type: str) -> HubSpotResponse:
        """Archive a custom object schema.

        Args:
            object_type: The custom object type name or ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.crm.schemas.core_api.archive(
                object_type=object_type  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived schema",
            )
        except Exception as e:
            return _handle_error(e, "archive_schema")

    # =====================================================================
    # CRM - Lists
    # =====================================================================

    def list_lists(
        self,
        limit: int = 25,
        offset: int | None = None,
    ) -> HubSpotResponse:
        """List all CRM lists.

        Args:
            limit: Max number of results per page.
            offset: Pagination offset.

        Returns:
            HubSpotResponse with paginated lists data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit}
            if offset is not None:
                kwargs["offset"] = offset
            result = self._sdk.crm.lists.lists_api.get_all(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed lists",
            )
        except Exception as e:
            return _handle_error(e, "list_lists")

    def get_list(self, list_id: str) -> HubSpotResponse:
        """Get a CRM list by ID.

        Args:
            list_id: The list ID.

        Returns:
            HubSpotResponse with the list data.
        """
        try:
            result = self._sdk.crm.lists.lists_api.get_by_id(
                list_id=list_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved list",
            )
        except Exception as e:
            return _handle_error(e, "get_list")

    def get_list_by_name(self, list_name: str) -> HubSpotResponse:
        """Get a CRM list by name.

        Args:
            list_name: The list name.

        Returns:
            HubSpotResponse with the list data.
        """
        try:
            result = self._sdk.crm.lists.lists_api.get_by_name(
                list_name=list_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved list by name",
            )
        except Exception as e:
            return _handle_error(e, "get_list_by_name")

    def create_list(
        self, list_create_request: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new CRM list.

        Args:
            list_create_request: Dict with list definition (``name``,
                ``objectTypeId``, ``processingType``, ``filterBranch``, etc.).

        Returns:
            HubSpotResponse with the created list data.
        """
        try:
            result = self._sdk.crm.lists.lists_api.create(
                list_create_request=list_create_request  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created list",
            )
        except Exception as e:
            return _handle_error(e, "create_list")

    def remove_list(self, list_id: str) -> HubSpotResponse:
        """Remove (delete) a CRM list.

        Args:
            list_id: The list ID.

        Returns:
            HubSpotResponse confirming the removal.
        """
        try:
            self._sdk.crm.lists.lists_api.remove(
                list_id=list_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"removed": True},
                message="Successfully removed list",
            )
        except Exception as e:
            return _handle_error(e, "remove_list")

    def search_lists(
        self, list_search_request: dict[str, Any]
    ) -> HubSpotResponse:
        """Search CRM lists.

        Args:
            list_search_request: Dict with search parameters.

        Returns:
            HubSpotResponse with matching lists.
        """
        try:
            result = self._sdk.crm.lists.lists_api.do_search(
                list_search_request=list_search_request  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully searched lists",
            )
        except Exception as e:
            return _handle_error(e, "search_lists")

    # ------------------------------------------------------------------
    # List Memberships
    # ------------------------------------------------------------------

    def get_list_members(
        self,
        list_id: str,
        limit: int = 100,
        after: str | None = None,
    ) -> HubSpotResponse:
        """Get members of a CRM list with pagination.

        Args:
            list_id: The list ID.
            limit: Max number of results per page.
            after: Cursor token for the next page.

        Returns:
            HubSpotResponse with paginated member data.
        """
        try:
            kwargs: dict[str, Any] = {"list_id": list_id, "limit": limit}
            if after is not None:
                kwargs["after"] = after
            result = self._sdk.crm.lists.memberships_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved list members",
            )
        except Exception as e:
            return _handle_error(e, "get_list_members")

    def add_list_members(
        self, list_id: str, request_body: list[int]
    ) -> HubSpotResponse:
        """Add members (record IDs) to a CRM list.

        Args:
            list_id: The list ID.
            request_body: List of record IDs to add.

        Returns:
            HubSpotResponse confirming the addition.
        """
        try:
            result = self._sdk.crm.lists.memberships_api.add(
                list_id=list_id, request_body=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully added members to list",
            )
        except Exception as e:
            return _handle_error(e, "add_list_members")

    def remove_list_members(
        self, list_id: str, request_body: list[int]
    ) -> HubSpotResponse:
        """Remove members (record IDs) from a CRM list.

        Args:
            list_id: The list ID.
            request_body: List of record IDs to remove.

        Returns:
            HubSpotResponse confirming the removal.
        """
        try:
            result = self._sdk.crm.lists.memberships_api.remove(
                list_id=list_id, request_body=request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully removed members from list",
            )
        except Exception as e:
            return _handle_error(e, "remove_list_members")

    # =====================================================================
    # CRM - Imports
    # =====================================================================

    def list_imports(
        self,
        limit: int = 25,
        after: str | None = None,
    ) -> HubSpotResponse:
        """List CRM imports with pagination.

        Args:
            limit: Max number of results per page.
            after: Cursor token for the next page.

        Returns:
            HubSpotResponse with paginated imports data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit}
            if after is not None:
                kwargs["after"] = after
            result = self._sdk.crm.imports.core_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed imports",
            )
        except Exception as e:
            return _handle_error(e, "list_imports")

    def get_import(self, import_id: str) -> HubSpotResponse:
        """Get a CRM import by ID.

        Args:
            import_id: The import ID.

        Returns:
            HubSpotResponse with the import data.
        """
        try:
            result = self._sdk.crm.imports.core_api.get_by_id(
                import_id=import_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved import",
            )
        except Exception as e:
            return _handle_error(e, "get_import")

    def create_import(
        self,
        import_request: dict[str, Any],
        files: Any = None,
    ) -> HubSpotResponse:
        """Start a new CRM import.

        Args:
            import_request: Dict with import configuration (mapping, file metadata, etc.).
            files: Optional file data to import.

        Returns:
            HubSpotResponse with the import job data.
        """
        try:
            kwargs: dict[str, Any] = {"import_request": import_request}
            if files is not None:
                kwargs["files"] = files
            result = self._sdk.crm.imports.core_api.create(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully started import",
            )
        except Exception as e:
            return _handle_error(e, "create_import")

    def cancel_import(self, import_id: str) -> HubSpotResponse:
        """Cancel a running CRM import.

        Args:
            import_id: The import ID.

        Returns:
            HubSpotResponse confirming the cancellation.
        """
        try:
            result = self._sdk.crm.imports.core_api.cancel(
                import_id=import_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully cancelled import",
            )
        except Exception as e:
            return _handle_error(e, "cancel_import")

    # =====================================================================
    # CRM - Exports
    # =====================================================================

    def start_export(
        self, public_export_request: dict[str, Any]
    ) -> HubSpotResponse:
        """Start a CRM data export.

        Args:
            public_export_request: Dict with export definition (``exportType``,
                ``format``, ``objectType``, ``objectProperties``, etc.).

        Returns:
            HubSpotResponse with the export task data.
        """
        try:
            result = self._sdk.crm.exports.public_exports_api.start(
                public_export_request=public_export_request  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully started export",
            )
        except Exception as e:
            return _handle_error(e, "start_export")

    def get_export_status(self, task_id: str) -> HubSpotResponse:
        """Get the status of a CRM export task.

        Args:
            task_id: The export task ID.

        Returns:
            HubSpotResponse with the export status data.
        """
        try:
            result = self._sdk.crm.exports.public_exports_api.get_status(
                task_id=task_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved export status",
            )
        except Exception as e:
            return _handle_error(e, "get_export_status")

    # =====================================================================
    # CRM - Timeline
    # =====================================================================

    def create_timeline_event(
        self, timeline_event: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a CRM timeline event.

        Args:
            timeline_event: Dict with timeline event data (``eventTemplateId``,
                ``objectId``, ``tokens``, etc.).

        Returns:
            HubSpotResponse with the created event data.
        """
        try:
            result = self._sdk.crm.timeline.events_api.create(
                timeline_event=timeline_event  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created timeline event",
            )
        except Exception as e:
            return _handle_error(e, "create_timeline_event")

    def get_timeline_event(
        self,
        app_id: int,
        event_template_id: str,
        event_id: str,
    ) -> HubSpotResponse:
        """Get a specific timeline event by ID.

        Args:
            app_id: The HubSpot app ID.
            event_template_id: The event template ID.
            event_id: The event ID.

        Returns:
            HubSpotResponse with the timeline event data.
        """
        try:
            result = self._sdk.crm.timeline.events_api.get_by_id(
                app_id=app_id,
                event_template_id=event_template_id,
                event_id=event_id,  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved timeline event",
            )
        except Exception as e:
            return _handle_error(e, "get_timeline_event")

    def list_timeline_templates(self, app_id: int) -> HubSpotResponse:
        """List all timeline event templates for an app.

        Args:
            app_id: The HubSpot app ID.

        Returns:
            HubSpotResponse with the list of templates.
        """
        try:
            result = self._sdk.crm.timeline.templates_api.get_all(
                app_id=app_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed timeline templates",
            )
        except Exception as e:
            return _handle_error(e, "list_timeline_templates")

    def get_timeline_template(
        self, app_id: int, event_template_id: str
    ) -> HubSpotResponse:
        """Get a specific timeline event template.

        Args:
            app_id: The HubSpot app ID.
            event_template_id: The event template ID.

        Returns:
            HubSpotResponse with the template data.
        """
        try:
            result = self._sdk.crm.timeline.templates_api.get_by_id(
                app_id=app_id, event_template_id=event_template_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved timeline template",
            )
        except Exception as e:
            return _handle_error(e, "get_timeline_template")

    def create_timeline_template(
        self,
        app_id: int,
        timeline_event_template_create_request: dict[str, Any],
    ) -> HubSpotResponse:
        """Create a new timeline event template.

        Args:
            app_id: The HubSpot app ID.
            timeline_event_template_create_request: Dict with template definition
                (``name``, ``objectType``, ``tokens``, etc.).

        Returns:
            HubSpotResponse with the created template data.
        """
        try:
            result = self._sdk.crm.timeline.templates_api.create(
                app_id=app_id,
                timeline_event_template_create_request=timeline_event_template_create_request,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created timeline template",
            )
        except Exception as e:
            return _handle_error(e, "create_timeline_template")

    # =====================================================================
    # CMS - Domains
    # =====================================================================

    def list_domains(self) -> HubSpotResponse:
        """List all CMS domains.

        Returns:
            HubSpotResponse with the list of domains.
        """
        try:
            result = self._sdk.cms.domains.domains_api.get_page()  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed domains",
            )
        except Exception as e:
            return _handle_error(e, "list_domains")

    def get_domain(self, domain_id: str) -> HubSpotResponse:
        """Get a CMS domain by ID.

        Args:
            domain_id: The domain ID.

        Returns:
            HubSpotResponse with the domain data.
        """
        try:
            result = self._sdk.cms.domains.domains_api.get_by_id(
                domain_id=domain_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved domain",
            )
        except Exception as e:
            return _handle_error(e, "get_domain")

    # =====================================================================
    # CMS - HubDB
    # =====================================================================

    def list_hubdb_tables(self) -> HubSpotResponse:
        """List all HubDB tables.

        Returns:
            HubSpotResponse with the list of HubDB tables.
        """
        try:
            result = self._sdk.cms.hubdb.tables_api.get_all_tables()  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed HubDB tables",
            )
        except Exception as e:
            return _handle_error(e, "list_hubdb_tables")

    def get_hubdb_table(self, table_id_or_name: str) -> HubSpotResponse:
        """Get a HubDB table by ID or name.

        Args:
            table_id_or_name: The table ID or name.

        Returns:
            HubSpotResponse with the table data.
        """
        try:
            result = self._sdk.cms.hubdb.tables_api.get_table_details(
                table_id_or_name=table_id_or_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved HubDB table",
            )
        except Exception as e:
            return _handle_error(e, "get_hubdb_table")

    def create_hubdb_table(
        self, hub_db_table_v3_request: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new HubDB table.

        Args:
            hub_db_table_v3_request: Dict with table definition (``name``, ``label``,
                ``columns``, etc.).

        Returns:
            HubSpotResponse with the created table data.
        """
        try:
            result = self._sdk.cms.hubdb.tables_api.create_table(
                hub_db_table_v3_request=hub_db_table_v3_request  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created HubDB table",
            )
        except Exception as e:
            return _handle_error(e, "create_hubdb_table")

    def archive_hubdb_table(self, table_id_or_name: str) -> HubSpotResponse:
        """Archive a HubDB table.

        Args:
            table_id_or_name: The table ID or name.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.cms.hubdb.tables_api.archive_table(
                table_id_or_name=table_id_or_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived HubDB table",
            )
        except Exception as e:
            return _handle_error(e, "archive_hubdb_table")

    def publish_hubdb_table(self, table_id_or_name: str) -> HubSpotResponse:
        """Publish a draft HubDB table.

        Args:
            table_id_or_name: The table ID or name.

        Returns:
            HubSpotResponse with the published table data.
        """
        try:
            result = self._sdk.cms.hubdb.tables_api.publish_draft_table(
                table_id_or_name=table_id_or_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully published HubDB table",
            )
        except Exception as e:
            return _handle_error(e, "publish_hubdb_table")

    # ------------------------------------------------------------------
    # HubDB Rows
    # ------------------------------------------------------------------

    def list_hubdb_rows(self, table_id_or_name: str) -> HubSpotResponse:
        """List all rows in a HubDB table.

        Args:
            table_id_or_name: The table ID or name.

        Returns:
            HubSpotResponse with the list of rows.
        """
        try:
            result = self._sdk.cms.hubdb.rows_api.get_table_rows(
                table_id_or_name=table_id_or_name  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed HubDB rows",
            )
        except Exception as e:
            return _handle_error(e, "list_hubdb_rows")

    def get_hubdb_row(
        self, table_id_or_name: str, row_id: str
    ) -> HubSpotResponse:
        """Get a specific row from a HubDB table.

        Args:
            table_id_or_name: The table ID or name.
            row_id: The row ID.

        Returns:
            HubSpotResponse with the row data.
        """
        try:
            result = self._sdk.cms.hubdb.rows_api.get_table_row(
                table_id_or_name=table_id_or_name, row_id=row_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved HubDB row",
            )
        except Exception as e:
            return _handle_error(e, "get_hubdb_row")

    def create_hubdb_row(
        self,
        table_id_or_name: str,
        hub_db_table_row_v3_request: dict[str, Any],
    ) -> HubSpotResponse:
        """Create a new row in a HubDB table.

        Args:
            table_id_or_name: The table ID or name.
            hub_db_table_row_v3_request: Dict with row data (``values``, ``path``, etc.).

        Returns:
            HubSpotResponse with the created row data.
        """
        try:
            result = self._sdk.cms.hubdb.rows_api.create_table_row(
                table_id_or_name=table_id_or_name,
                hub_db_table_row_v3_request=hub_db_table_row_v3_request,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created HubDB row",
            )
        except Exception as e:
            return _handle_error(e, "create_hubdb_row")

    def update_hubdb_row(
        self,
        table_id_or_name: str,
        row_id: str,
        hub_db_table_row_v3_request: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a draft row in a HubDB table.

        Args:
            table_id_or_name: The table ID or name.
            row_id: The row ID.
            hub_db_table_row_v3_request: Dict with updated row data.

        Returns:
            HubSpotResponse with the updated row data.
        """
        try:
            result = self._sdk.cms.hubdb.rows_api.update_draft_table_row(
                table_id_or_name=table_id_or_name,
                row_id=row_id,
                hub_db_table_row_v3_request=hub_db_table_row_v3_request,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated HubDB row",
            )
        except Exception as e:
            return _handle_error(e, "update_hubdb_row")

    # =====================================================================
    # CMS - URL Redirects
    # =====================================================================

    def list_url_redirects(self) -> HubSpotResponse:
        """List all CMS URL redirects.

        Returns:
            HubSpotResponse with the list of URL redirects.
        """
        try:
            result = self._sdk.cms.url_redirects.redirects_api.get_page()  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed URL redirects",
            )
        except Exception as e:
            return _handle_error(e, "list_url_redirects")

    def get_url_redirect(self, url_redirect_id: str) -> HubSpotResponse:
        """Get a CMS URL redirect by ID.

        Args:
            url_redirect_id: The URL redirect ID.

        Returns:
            HubSpotResponse with the redirect data.
        """
        try:
            result = self._sdk.cms.url_redirects.redirects_api.get_by_id(
                url_redirect_id=url_redirect_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved URL redirect",
            )
        except Exception as e:
            return _handle_error(e, "get_url_redirect")

    def create_url_redirect(
        self, url_mapping_create_request_body: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new CMS URL redirect.

        Args:
            url_mapping_create_request_body: Dict with redirect definition
                (``routePrefix``, ``destination``, ``redirectStyle``, etc.).

        Returns:
            HubSpotResponse with the created redirect data.
        """
        try:
            result = self._sdk.cms.url_redirects.redirects_api.create(
                url_mapping_create_request_body=url_mapping_create_request_body  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created URL redirect",
            )
        except Exception as e:
            return _handle_error(e, "create_url_redirect")

    def update_url_redirect(
        self,
        url_redirect_id: str,
        url_mapping: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a CMS URL redirect.

        Args:
            url_redirect_id: The URL redirect ID.
            url_mapping: Dict with fields to update.

        Returns:
            HubSpotResponse with the updated redirect data.
        """
        try:
            result = self._sdk.cms.url_redirects.redirects_api.update(
                url_redirect_id=url_redirect_id,
                url_mapping=url_mapping,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated URL redirect",
            )
        except Exception as e:
            return _handle_error(e, "update_url_redirect")

    def archive_url_redirect(self, url_redirect_id: str) -> HubSpotResponse:
        """Archive a CMS URL redirect.

        Args:
            url_redirect_id: The URL redirect ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.cms.url_redirects.redirects_api.archive(
                url_redirect_id=url_redirect_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived URL redirect",
            )
        except Exception as e:
            return _handle_error(e, "archive_url_redirect")

    # =====================================================================
    # CMS - Audit Logs
    # =====================================================================

    def list_audit_logs(
        self,
        object_id: list[str] | None = None,
        object_type: list[str] | None = None,
        user_id: list[str] | None = None,
        after: str | None = None,
        before: str | None = None,
        sort: list[str] | None = None,
        limit: int | None = None,
    ) -> HubSpotResponse:
        """List CMS audit log entries.

        Args:
            object_id: Filter by object IDs.
            object_type: Filter by object types.
            user_id: Filter by user IDs.
            after: Only return entries after this timestamp.
            before: Only return entries before this timestamp.
            sort: Sort order definitions.
            limit: Max number of results.

        Returns:
            HubSpotResponse with audit log entries.
        """
        try:
            kwargs: dict[str, Any] = {}
            if object_id is not None:
                kwargs["object_id"] = object_id
            if object_type is not None:
                kwargs["object_type"] = object_type
            if user_id is not None:
                kwargs["user_id"] = user_id
            if after is not None:
                kwargs["after"] = after
            if before is not None:
                kwargs["before"] = before
            if sort is not None:
                kwargs["sort"] = sort
            if limit is not None:
                kwargs["limit"] = limit
            result = self._sdk.cms.audit_logs.audit_logs_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed audit logs",
            )
        except Exception as e:
            return _handle_error(e, "list_audit_logs")

    # =====================================================================
    # Marketing - Forms
    # =====================================================================

    def list_forms(
        self,
        limit: int = 20,
        after: str | None = None,
    ) -> HubSpotResponse:
        """List marketing forms with pagination.

        Args:
            limit: Max number of results per page.
            after: Cursor token for the next page.

        Returns:
            HubSpotResponse with paginated forms data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit}
            if after is not None:
                kwargs["after"] = after
            result = self._sdk.marketing.forms.forms_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed forms",
            )
        except Exception as e:
            return _handle_error(e, "list_forms")

    def get_form(self, form_id: str) -> HubSpotResponse:
        """Get a marketing form by ID.

        Args:
            form_id: The form ID.

        Returns:
            HubSpotResponse with the form data.
        """
        try:
            result = self._sdk.marketing.forms.forms_api.get_by_id(
                form_id=form_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved form",
            )
        except Exception as e:
            return _handle_error(e, "get_form")

    def create_form(
        self, form_definition_create_request_base: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new marketing form.

        Args:
            form_definition_create_request_base: Dict with form definition
                (``name``, ``formType``, ``fieldGroups``, etc.).

        Returns:
            HubSpotResponse with the created form data.
        """
        try:
            result = self._sdk.marketing.forms.forms_api.create(
                form_definition_create_request_base=form_definition_create_request_base  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created form",
            )
        except Exception as e:
            return _handle_error(e, "create_form")

    def update_form(
        self,
        form_id: str,
        form_definition_patch_request: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a marketing form.

        Args:
            form_id: The form ID.
            form_definition_patch_request: Dict with fields to update.

        Returns:
            HubSpotResponse with the updated form data.
        """
        try:
            result = self._sdk.marketing.forms.forms_api.update(
                form_id=form_id,
                form_definition_patch_request=form_definition_patch_request,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated form",
            )
        except Exception as e:
            return _handle_error(e, "update_form")

    def archive_form(self, form_id: str) -> HubSpotResponse:
        """Archive a marketing form.

        Args:
            form_id: The form ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.marketing.forms.forms_api.archive(
                form_id=form_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived form",
            )
        except Exception as e:
            return _handle_error(e, "archive_form")

    # =====================================================================
    # Marketing - Emails
    # =====================================================================

    def list_marketing_emails(
        self,
        limit: int = 20,
        after: str | None = None,
    ) -> HubSpotResponse:
        """List marketing emails with pagination.

        Args:
            limit: Max number of results per page.
            after: Cursor token for the next page.

        Returns:
            HubSpotResponse with paginated marketing emails data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit}
            if after is not None:
                kwargs["after"] = after
            result = self._sdk.marketing.emails.marketing_emails_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed marketing emails",
            )
        except Exception as e:
            return _handle_error(e, "list_marketing_emails")

    def get_marketing_email(self, email_id: str) -> HubSpotResponse:
        """Get a marketing email by ID.

        Args:
            email_id: The email ID.

        Returns:
            HubSpotResponse with the email data.
        """
        try:
            result = self._sdk.marketing.emails.marketing_emails_api.get_by_id(
                email_id=email_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved marketing email",
            )
        except Exception as e:
            return _handle_error(e, "get_marketing_email")

    def create_marketing_email(
        self, email_create_request: dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new marketing email.

        Args:
            email_create_request: Dict with email definition (``name``, ``subject``,
                ``body``, etc.).

        Returns:
            HubSpotResponse with the created email data.
        """
        try:
            result = self._sdk.marketing.emails.marketing_emails_api.create(
                email_create_request=email_create_request  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully created marketing email",
            )
        except Exception as e:
            return _handle_error(e, "create_marketing_email")

    def update_marketing_email(
        self,
        email_id: str,
        email_update_request: dict[str, Any],
    ) -> HubSpotResponse:
        """Update a marketing email.

        Args:
            email_id: The email ID.
            email_update_request: Dict with fields to update.

        Returns:
            HubSpotResponse with the updated email data.
        """
        try:
            result = self._sdk.marketing.emails.marketing_emails_api.update(
                email_id=email_id,
                email_update_request=email_update_request,  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully updated marketing email",
            )
        except Exception as e:
            return _handle_error(e, "update_marketing_email")

    def archive_marketing_email(self, email_id: str) -> HubSpotResponse:
        """Archive a marketing email.

        Args:
            email_id: The email ID.

        Returns:
            HubSpotResponse confirming the archive.
        """
        try:
            self._sdk.marketing.emails.marketing_emails_api.archive(
                email_id=email_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data={"archived": True},
                message="Successfully archived marketing email",
            )
        except Exception as e:
            return _handle_error(e, "archive_marketing_email")

    # =====================================================================
    # Marketing - Transactional Email
    # =====================================================================

    def send_transactional_email(
        self, public_single_send_request_egg: dict[str, Any]
    ) -> HubSpotResponse:
        """Send a transactional email via the single-send API.

        Args:
            public_single_send_request_egg: Dict with send request data
                (``emailId``, ``message``, ``contactProperties``, etc.).

        Returns:
            HubSpotResponse with the send result.
        """
        try:
            result = self._sdk.marketing.transactional.single_send_api.send_email(
                public_single_send_request_egg=public_single_send_request_egg  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully sent transactional email",
            )
        except Exception as e:
            return _handle_error(e, "send_transactional_email")

    # =====================================================================
    # Settings - Users
    # =====================================================================

    def list_users(
        self,
        limit: int = 100,
        after: str | None = None,
    ) -> HubSpotResponse:
        """List HubSpot account users with pagination.

        Args:
            limit: Max number of results per page.
            after: Cursor token for the next page.

        Returns:
            HubSpotResponse with paginated users data.
        """
        try:
            kwargs: dict[str, Any] = {"limit": limit}
            if after is not None:
                kwargs["after"] = after
            result = self._sdk.settings.users.users_api.get_page(**kwargs)  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed users",
            )
        except Exception as e:
            return _handle_error(e, "list_users")

    def get_user(self, user_id: str) -> HubSpotResponse:
        """Get a HubSpot account user by ID.

        Args:
            user_id: The user ID.

        Returns:
            HubSpotResponse with the user data.
        """
        try:
            result = self._sdk.settings.users.users_api.get_by_id(
                user_id=user_id  # type: ignore[reportUnknownMemberType]
            )
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully retrieved user",
            )
        except Exception as e:
            return _handle_error(e, "get_user")

    # ------------------------------------------------------------------
    # Roles & Teams
    # ------------------------------------------------------------------

    def list_roles(self) -> HubSpotResponse:
        """List all roles in the HubSpot account.

        Returns:
            HubSpotResponse with the list of roles.
        """
        try:
            result = self._sdk.settings.users.roles_api.get_all()  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed roles",
            )
        except Exception as e:
            return _handle_error(e, "list_roles")

    def list_teams(self) -> HubSpotResponse:
        """List all teams in the HubSpot account.

        Returns:
            HubSpotResponse with the list of teams.
        """
        try:
            result = self._sdk.settings.users.teams_api.get_all()  # type: ignore[reportUnknownMemberType]
            return HubSpotResponse(
                success=True,
                data=_to_dict(result),
                message="Successfully listed teams",
            )
        except Exception as e:
            return _handle_error(e, "list_teams")

    # =====================================================================
    # Events - Custom Behavioural Events
    # =====================================================================

    def send_custom_event(
        self, custom_event_data: dict[str, Any]
    ) -> HubSpotResponse:
        """Send a custom behavioural event.

        Args:
            custom_event_data: Dict with event data (``eventName``, ``objectId``,
                ``properties``, etc.).

        Returns:
            HubSpotResponse confirming the event was sent.
        """
        try:
            self._sdk.events.send.basic_api.send(
                custom_event_data=custom_event_data  # type: ignore[reportUnknownMemberType,reportArgumentType]
            )
            return HubSpotResponse(
                success=True,
                data={"sent": True},
                message="Successfully sent custom event",
            )
        except Exception as e:
            return _handle_error(e, "send_custom_event")
