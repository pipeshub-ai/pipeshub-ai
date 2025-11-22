import json
from typing import Any
from urllib.parse import urlencode

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.hubspot.hubspot import HubSpotClient, HubSpotResponse


class HubSpotDataSource:
    """Auto-generated HubSpot API client wrapper.

    Provides async methods for ALL HubSpot API endpoints:
    - CRM APIs (Contacts, Companies, Deals, Tickets, Products, etc.)
    - Activities APIs (Calls, Emails, Meetings, Notes, Tasks)
    - Marketing APIs (Emails, Campaigns, Forms, Events)
    - Automation APIs (Workflows, Actions, Sequences)
    - CMS APIs (Pages, Blog Posts, HubDB, Domains)
    - Conversations APIs (Messages, Visitor Identification)
    - Events APIs (Custom Events, Event Definitions)
    - Settings APIs (Business Units, User Provisioning)
    - Integration APIs (Webhooks, CRM Extensions)
    - Communication Preferences APIs (Subscriptions)
    - Files APIs (File Management)
    - OAuth & Authentication APIs
    - Meetings APIs (Scheduler)

    All methods return HubSpotResponse objects with standardized success/data/error format.
    All parameters are explicitly typed - no **kwargs usage.
    """

    def __init__(self, client: HubSpotClient) -> None:
        """Initialize with HubSpotClient."""
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_data_source(self) -> "HubSpotDataSource":
        """Return the data source instance."""
        return self

    async def list_contacts(
        self,
        limit: int | None = None,
        after: str | None = None,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        archived: bool | None = None,
    ) -> HubSpotResponse:
        """List contacts with filtering and pagination

        Args:
            limit: Maximum number of results per page (max 100)
            after: Cursor for pagination
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            archived: Whether to return only archived contacts

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if limit is not None:
            query_params.append(("limit", str(limit)))
        if after is not None:
            query_params.append(("after", str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if archived is not None:
            query_params.append(("archived", "true" if archived else "false"))

        url = self.base_url + "/crm/v3/objects/contacts"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_contact(
        self,
        contact_id: str,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Get a single contact by ID

        Args:
            contact_id: Contact ID
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/contacts/{contact_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_contact(
        self,
        properties: dict[str, Any],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new contact

        Args:
            properties: Contact properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/contacts"

        body = {}
        body["properties"] = properties
        if associations is not None:
            body["associations"] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_contact(
        self,
        contact_id: str,
        properties: dict[str, Any],
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Update a contact

        Args:
            contact_id: Contact ID
            properties: Contact properties to update
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/contacts/{contact_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body["properties"] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_contact(
        self,
        contact_id: str,
    ) -> HubSpotResponse:
        """Delete a contact

        Args:
            contact_id: Contact ID

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + f"/crm/v3/objects/contacts/{contact_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_contacts(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Create multiple contacts

        Args:
            inputs: List of contact objects to create

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/contacts/batch/create"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_contacts(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Update multiple contacts

        Args:
            inputs: List of contact objects to update

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/contacts/batch/update"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_contacts(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Delete multiple contacts

        Args:
            inputs: List of contact identifiers to delete

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/contacts/batch/archive"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_contacts(
        self,
        filter_groups: list[dict[str, Any]],
        sorts: list[dict[str, str]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> HubSpotResponse:
        """Search contacts using filters

        Args:
            filter_groups: Filter groups for search
            sorts: Sort criteria
            query: Search query string
            properties: Properties to retrieve
            limit: Maximum results per page
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/contacts/search"

        body = {}
        body["filterGroups"] = filter_groups
        if sorts is not None:
            body["sorts"] = sorts
        if query is not None:
            body["query"] = query
        if properties is not None:
            body["properties"] = properties
        if limit is not None:
            body["limit"] = limit
        if after is not None:
            body["after"] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_companies(
        self,
        limit: int | None = None,
        after: str | None = None,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        archived: bool | None = None,
    ) -> HubSpotResponse:
        """List companies with filtering and pagination

        Args:
            limit: Maximum number of results per page (max 100)
            after: Cursor for pagination
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            archived: Whether to return only archived companies

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if limit is not None:
            query_params.append(("limit", str(limit)))
        if after is not None:
            query_params.append(("after", str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if archived is not None:
            query_params.append(("archived", "true" if archived else "false"))

        url = self.base_url + "/crm/v3/objects/companies"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_company(
        self,
        company_id: str,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Get a single company by ID

        Args:
            company_id: Company ID
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/companies/{company_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_company(
        self,
        properties: dict[str, Any],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new company

        Args:
            properties: Company properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/companies"

        body = {}
        body["properties"] = properties
        if associations is not None:
            body["associations"] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_company(
        self,
        company_id: str,
        properties: dict[str, Any],
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Update a company

        Args:
            company_id: Company ID
            properties: Company properties to update
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/companies/{company_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body["properties"] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_company(
        self,
        company_id: str,
    ) -> HubSpotResponse:
        """Delete a company

        Args:
            company_id: Company ID

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + f"/crm/v3/objects/companies/{company_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_companies(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Create multiple companies

        Args:
            inputs: List of company objects to create

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/companies/batch/create"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_companies(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Update multiple companies

        Args:
            inputs: List of company objects to update

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/companies/batch/update"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_companies(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Delete multiple companies

        Args:
            inputs: List of company identifiers to delete

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/companies/batch/archive"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_companies(
        self,
        filter_groups: list[dict[str, Any]],
        sorts: list[dict[str, str]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> HubSpotResponse:
        """Search companies using filters

        Args:
            filter_groups: Filter groups for search
            sorts: Sort criteria
            query: Search query string
            properties: Properties to retrieve
            limit: Maximum results per page
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/companies/search"

        body = {}
        body["filterGroups"] = filter_groups
        if sorts is not None:
            body["sorts"] = sorts
        if query is not None:
            body["query"] = query
        if properties is not None:
            body["properties"] = properties
        if limit is not None:
            body["limit"] = limit
        if after is not None:
            body["after"] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_deals(
        self,
        limit: int | None = None,
        after: str | None = None,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        archived: bool | None = None,
    ) -> HubSpotResponse:
        """List deals with filtering and pagination

        Args:
            limit: Maximum number of results per page (max 100)
            after: Cursor for pagination
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            archived: Whether to return only archived deals

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if limit is not None:
            query_params.append(("limit", str(limit)))
        if after is not None:
            query_params.append(("after", str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if archived is not None:
            query_params.append(("archived", "true" if archived else "false"))

        url = self.base_url + "/crm/v3/objects/deals"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_deal(
        self,
        deal_id: str,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Get a single deal by ID

        Args:
            deal_id: Deal ID
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/deals/{deal_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_deal(
        self,
        properties: dict[str, Any],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new deal

        Args:
            properties: Deal properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/deals"

        body = {}
        body["properties"] = properties
        if associations is not None:
            body["associations"] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_deal(
        self,
        deal_id: str,
        properties: dict[str, Any],
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Update a deal

        Args:
            deal_id: Deal ID
            properties: Deal properties to update
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/deals/{deal_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body["properties"] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_deal(
        self,
        deal_id: str,
    ) -> HubSpotResponse:
        """Delete a deal

        Args:
            deal_id: Deal ID

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + f"/crm/v3/objects/deals/{deal_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_deals(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Create multiple deals

        Args:
            inputs: List of deal objects to create

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/deals/batch/create"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_deals(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Update multiple deals

        Args:
            inputs: List of deal objects to update

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/deals/batch/update"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_deals(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Delete multiple deals

        Args:
            inputs: List of deal identifiers to delete

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/deals/batch/archive"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_deals(
        self,
        filter_groups: list[dict[str, Any]],
        sorts: list[dict[str, str]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> HubSpotResponse:
        """Search deals using filters

        Args:
            filter_groups: Filter groups for search
            sorts: Sort criteria
            query: Search query string
            properties: Properties to retrieve
            limit: Maximum results per page
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/deals/search"

        body = {}
        body["filterGroups"] = filter_groups
        if sorts is not None:
            body["sorts"] = sorts
        if query is not None:
            body["query"] = query
        if properties is not None:
            body["properties"] = properties
        if limit is not None:
            body["limit"] = limit
        if after is not None:
            body["after"] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_tickets(
        self,
        limit: int | None = None,
        after: str | None = None,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        archived: bool | None = None,
    ) -> HubSpotResponse:
        """List tickets with filtering and pagination

        Args:
            limit: Maximum number of results per page (max 100)
            after: Cursor for pagination
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            archived: Whether to return only archived tickets

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if limit is not None:
            query_params.append(("limit", str(limit)))
        if after is not None:
            query_params.append(("after", str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if archived is not None:
            query_params.append(("archived", "true" if archived else "false"))

        url = self.base_url + "/crm/v3/objects/tickets"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_ticket(
        self,
        ticket_id: str,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Get a single ticket by ID

        Args:
            ticket_id: Ticket ID
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/tickets/{ticket_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_ticket(
        self,
        properties: dict[str, Any],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new ticket

        Args:
            properties: Ticket properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/tickets"

        body = {}
        body["properties"] = properties
        if associations is not None:
            body["associations"] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_ticket(
        self,
        ticket_id: str,
        properties: dict[str, Any],
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Update a ticket

        Args:
            ticket_id: Ticket ID
            properties: Ticket properties to update
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/tickets/{ticket_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body["properties"] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_ticket(
        self,
        ticket_id: str,
    ) -> HubSpotResponse:
        """Delete a ticket

        Args:
            ticket_id: Ticket ID

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + f"/crm/v3/objects/tickets/{ticket_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_tickets(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Create multiple tickets

        Args:
            inputs: List of ticket objects to create

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/tickets/batch/create"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_tickets(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Update multiple tickets

        Args:
            inputs: List of ticket objects to update

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/tickets/batch/update"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_tickets(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Delete multiple tickets

        Args:
            inputs: List of ticket identifiers to delete

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/tickets/batch/archive"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_tickets(
        self,
        filter_groups: list[dict[str, Any]],
        sorts: list[dict[str, str]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> HubSpotResponse:
        """Search tickets using filters

        Args:
            filter_groups: Filter groups for search
            sorts: Sort criteria
            query: Search query string
            properties: Properties to retrieve
            limit: Maximum results per page
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/tickets/search"

        body = {}
        body["filterGroups"] = filter_groups
        if sorts is not None:
            body["sorts"] = sorts
        if query is not None:
            body["query"] = query
        if properties is not None:
            body["properties"] = properties
        if limit is not None:
            body["limit"] = limit
        if after is not None:
            body["after"] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_products(
        self,
        limit: int | None = None,
        after: str | None = None,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        archived: bool | None = None,
    ) -> HubSpotResponse:
        """List products with filtering and pagination

        Args:
            limit: Maximum number of results per page (max 100)
            after: Cursor for pagination
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            archived: Whether to return only archived products

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if limit is not None:
            query_params.append(("limit", str(limit)))
        if after is not None:
            query_params.append(("after", str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if archived is not None:
            query_params.append(("archived", "true" if archived else "false"))

        url = self.base_url + "/crm/v3/objects/products"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_product(
        self,
        product_id: str,
        properties: list[str] | None = None,
        property_history: list[str] | None = None,
        associations: list[str] | None = None,
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Get a single product by ID

        Args:
            product_id: Product ID
            properties: List of properties to retrieve
            property_history: List of properties to include historical values
            associations: List of associated object types to retrieve
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if property_history is not None:
            for item in property_history:
                query_params.append(("propertyHistory", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/products/{product_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_product(
        self,
        properties: dict[str, Any],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new product

        Args:
            properties: Product properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/products"

        body = {}
        body["properties"] = properties
        if associations is not None:
            body["associations"] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_product(
        self,
        product_id: str,
        properties: dict[str, Any],
        id_property: str | None = None,
    ) -> HubSpotResponse:
        """Update a product

        Args:
            product_id: Product ID
            properties: Product properties to update
            id_property: Name of property to use as unique identifier

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if id_property is not None:
            query_params.append(("idProperty", str(id_property)))

        url = self.base_url + f"/crm/v3/objects/products/{product_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body["properties"] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_product(
        self,
        product_id: str,
    ) -> HubSpotResponse:
        """Delete a product

        Args:
            product_id: Product ID

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + f"/crm/v3/objects/products/{product_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_products(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Create multiple products

        Args:
            inputs: List of product objects to create

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/products/batch/create"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_products(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Update multiple products

        Args:
            inputs: List of product objects to update

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/products/batch/update"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_products(
        self,
        inputs: list[dict[str, Any]],
    ) -> HubSpotResponse:
        """Delete multiple products

        Args:
            inputs: List of product identifiers to delete

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/products/batch/archive"

        body = {}
        body["inputs"] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_products(
        self,
        filter_groups: list[dict[str, Any]],
        sorts: list[dict[str, str]] | None = None,
        query: str | None = None,
        properties: list[str] | None = None,
        limit: int | None = None,
        after: str | None = None,
    ) -> HubSpotResponse:
        """Search products using filters

        Args:
            filter_groups: Filter groups for search
            sorts: Sort criteria
            query: Search query string
            properties: Properties to retrieve
            limit: Maximum results per page
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/products/search"

        body = {}
        body["filterGroups"] = filter_groups
        if sorts is not None:
            body["sorts"] = sorts
        if query is not None:
            body["query"] = query
        if properties is not None:
            body["properties"] = properties
        if limit is not None:
            body["limit"] = limit
        if after is not None:
            body["after"] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_line_items(
        self,
        limit: int | None = None,
        after: str | None = None,
        properties: list[str] | None = None,
        associations: list[str] | None = None,
        archived: bool | None = None,
    ) -> HubSpotResponse:
        """List line items with filtering and pagination

        Args:
            limit: Maximum number of results per page
            after: Cursor for pagination
            properties: List of properties to retrieve
            associations: List of associated object types
            archived: Whether to return archived line items

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if limit is not None:
            query_params.append(("limit", str(limit)))
        if after is not None:
            query_params.append(("after", str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))
        if archived is not None:
            query_params.append(("archived", "true" if archived else "false"))

        url = self.base_url + "/crm/v3/objects/line_items"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_line_item(
        self,
        line_item_id: str,
        properties: list[str] | None = None,
        associations: list[str] | None = None,
    ) -> HubSpotResponse:
        """Get a single line item by ID

        Args:
            line_item_id: Line item ID
            properties: List of properties to retrieve
            associations: List of associated object types

        Returns:
            HubSpotResponse with operation result

        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(("properties", item))
        if associations is not None:
            for item in associations:
                query_params.append(("associations", item))

        url = self.base_url + f"/crm/v3/objects/line_items/{line_item_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_line_item(
        self,
        properties: dict[str, Any],
        associations: list[dict[str, Any]] | None = None,
    ) -> HubSpotResponse:
        """Create a new line item

        Args:
            properties: Line item properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result

        """
        url = self.base_url + "/crm/v3/objects/line_items"

        body = {}
        body["properties"] = properties
        if associations is not None:
            body["associations"] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))
