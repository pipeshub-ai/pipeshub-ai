import json
from typing import Any, Dict, List, Optional
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
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'HubSpotDataSource':
        """Return the data source instance."""
        return self

    async def list_contacts(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
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
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/contacts"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_contact(
        self,
        contact_id: str,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        id_property: Optional[str] = None
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
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if id_property is not None:
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/contacts/{contact_id}".format(contact_id=contact_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_contact(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
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
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_contact(
        self,
        contact_id: str,
        properties: Dict[str, Any],
        id_property: Optional[str] = None
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
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/contacts/{contact_id}".format(contact_id=contact_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_contact(
        self,
        contact_id: str
    ) -> HubSpotResponse:
        """Delete a contact

        Args:
            contact_id: Contact ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/contacts/{contact_id}".format(contact_id=contact_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_contacts(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple contacts

        Args:
            inputs: List of contact objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/contacts/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_contacts(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple contacts

        Args:
            inputs: List of contact objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/contacts/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_contacts(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple contacts

        Args:
            inputs: List of contact identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/contacts/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_contacts(
        self,
        filter_groups: List[Dict[str, Any]],
        sorts: Optional[List[Dict[str, str]]] = None,
        query: Optional[str] = None,
        properties: Optional[List[str]] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None
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
        body['filterGroups'] = filter_groups
        if sorts is not None:
            body['sorts'] = sorts
        if query is not None:
            body['query'] = query
        if properties is not None:
            body['properties'] = properties
        if limit is not None:
            body['limit'] = limit
        if after is not None:
            body['after'] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_companies(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
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
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/companies"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_company(
        self,
        company_id: str,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        id_property: Optional[str] = None
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
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if id_property is not None:
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/companies/{company_id}".format(company_id=company_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_company(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
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
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_company(
        self,
        company_id: str,
        properties: Dict[str, Any],
        id_property: Optional[str] = None
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
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/companies/{company_id}".format(company_id=company_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_company(
        self,
        company_id: str
    ) -> HubSpotResponse:
        """Delete a company

        Args:
            company_id: Company ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/companies/{company_id}".format(company_id=company_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_companies(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple companies

        Args:
            inputs: List of company objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/companies/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_companies(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple companies

        Args:
            inputs: List of company objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/companies/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_companies(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple companies

        Args:
            inputs: List of company identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/companies/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_companies(
        self,
        filter_groups: List[Dict[str, Any]],
        sorts: Optional[List[Dict[str, str]]] = None,
        query: Optional[str] = None,
        properties: Optional[List[str]] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None
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
        body['filterGroups'] = filter_groups
        if sorts is not None:
            body['sorts'] = sorts
        if query is not None:
            body['query'] = query
        if properties is not None:
            body['properties'] = properties
        if limit is not None:
            body['limit'] = limit
        if after is not None:
            body['after'] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_deals(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
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
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/deals"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_deal(
        self,
        deal_id: str,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        id_property: Optional[str] = None
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
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if id_property is not None:
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/deals/{deal_id}".format(deal_id=deal_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_deal(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
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
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_deal(
        self,
        deal_id: str,
        properties: Dict[str, Any],
        id_property: Optional[str] = None
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
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/deals/{deal_id}".format(deal_id=deal_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_deal(
        self,
        deal_id: str
    ) -> HubSpotResponse:
        """Delete a deal

        Args:
            deal_id: Deal ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/deals/{deal_id}".format(deal_id=deal_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_deals(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple deals

        Args:
            inputs: List of deal objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/deals/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_deals(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple deals

        Args:
            inputs: List of deal objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/deals/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_deals(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple deals

        Args:
            inputs: List of deal identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/deals/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_deals(
        self,
        filter_groups: List[Dict[str, Any]],
        sorts: Optional[List[Dict[str, str]]] = None,
        query: Optional[str] = None,
        properties: Optional[List[str]] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None
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
        body['filterGroups'] = filter_groups
        if sorts is not None:
            body['sorts'] = sorts
        if query is not None:
            body['query'] = query
        if properties is not None:
            body['properties'] = properties
        if limit is not None:
            body['limit'] = limit
        if after is not None:
            body['after'] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_tickets(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
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
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/tickets"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_ticket(
        self,
        ticket_id: str,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        id_property: Optional[str] = None
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
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if id_property is not None:
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/tickets/{ticket_id}".format(ticket_id=ticket_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_ticket(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
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
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_ticket(
        self,
        ticket_id: str,
        properties: Dict[str, Any],
        id_property: Optional[str] = None
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
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/tickets/{ticket_id}".format(ticket_id=ticket_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_ticket(
        self,
        ticket_id: str
    ) -> HubSpotResponse:
        """Delete a ticket

        Args:
            ticket_id: Ticket ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/tickets/{ticket_id}".format(ticket_id=ticket_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_tickets(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple tickets

        Args:
            inputs: List of ticket objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/tickets/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_tickets(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple tickets

        Args:
            inputs: List of ticket objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/tickets/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_tickets(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple tickets

        Args:
            inputs: List of ticket identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/tickets/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_tickets(
        self,
        filter_groups: List[Dict[str, Any]],
        sorts: Optional[List[Dict[str, str]]] = None,
        query: Optional[str] = None,
        properties: Optional[List[str]] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None
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
        body['filterGroups'] = filter_groups
        if sorts is not None:
            body['sorts'] = sorts
        if query is not None:
            body['query'] = query
        if properties is not None:
            body['properties'] = properties
        if limit is not None:
            body['limit'] = limit
        if after is not None:
            body['after'] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_products(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
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
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/products"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_product(
        self,
        product_id: str,
        properties: Optional[List[str]] = None,
        property_history: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        id_property: Optional[str] = None
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
                query_params.append(('properties', item))
        if property_history is not None:
            for item in property_history:
                query_params.append(('propertyHistory', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if id_property is not None:
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/products/{product_id}".format(product_id=product_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_product(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
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
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_product(
        self,
        product_id: str,
        properties: Dict[str, Any],
        id_property: Optional[str] = None
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
            query_params.append(('idProperty', str(id_property)))

        url = self.base_url + "/crm/v3/objects/products/{product_id}".format(product_id=product_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_product(
        self,
        product_id: str
    ) -> HubSpotResponse:
        """Delete a product

        Args:
            product_id: Product ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/products/{product_id}".format(product_id=product_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_products(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple products

        Args:
            inputs: List of product objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/products/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_products(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple products

        Args:
            inputs: List of product objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/products/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_products(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple products

        Args:
            inputs: List of product identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/products/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def search_products(
        self,
        filter_groups: List[Dict[str, Any]],
        sorts: Optional[List[Dict[str, str]]] = None,
        query: Optional[str] = None,
        properties: Optional[List[str]] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None
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
        body['filterGroups'] = filter_groups
        if sorts is not None:
            body['sorts'] = sorts
        if query is not None:
            body['query'] = query
        if properties is not None:
            body['properties'] = properties
        if limit is not None:
            body['limit'] = limit
        if after is not None:
            body['after'] = after

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_line_items(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
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
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/line_items"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_line_item(
        self,
        line_item_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
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
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))

        url = self.base_url + "/crm/v3/objects/line_items/{line_item_id}".format(line_item_id=line_item_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_line_item(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
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
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_line_item(
        self,
        line_item_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a line item

        Args:
            line_item_id: Line item ID
            properties: Line item properties to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/line_items/{line_item_id}".format(line_item_id=line_item_id)

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_line_item(
        self,
        line_item_id: str
    ) -> HubSpotResponse:
        """Delete a line item

        Args:
            line_item_id: Line item ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/line_items/{line_item_id}".format(line_item_id=line_item_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_line_items(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple line items

        Args:
            inputs: List of line item objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/line_items/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_line_items(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple line items

        Args:
            inputs: List of line item objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/line_items/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_line_items(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple line items

        Args:
            inputs: List of line item identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/line_items/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_quotes(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """List quotes with filtering and pagination

        Args:
            limit: Maximum number of results per page
            after: Cursor for pagination
            properties: List of properties to retrieve
            associations: List of associated object types
            archived: Whether to return archived quotes

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if limit is not None:
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/quotes"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_quote(
        self,
        quote_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get a single quote by ID

        Args:
            quote_id: Quote ID
            properties: List of properties to retrieve
            associations: List of associated object types

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))

        url = self.base_url + "/crm/v3/objects/quotes/{quote_id}".format(quote_id=quote_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_quote(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new quote

        Args:
            properties: Quote properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/quotes"

        body = {}
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_quote(
        self,
        quote_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a quote

        Args:
            quote_id: Quote ID
            properties: Quote properties to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/quotes/{quote_id}".format(quote_id=quote_id)

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_quote(
        self,
        quote_id: str
    ) -> HubSpotResponse:
        """Delete a quote

        Args:
            quote_id: Quote ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/quotes/{quote_id}".format(quote_id=quote_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_quotes(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple quotes

        Args:
            inputs: List of quote objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/quotes/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_quotes(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple quotes

        Args:
            inputs: List of quote objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/quotes/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_quotes(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple quotes

        Args:
            inputs: List of quote identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/quotes/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_calls(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """List calls with filtering and pagination

        Args:
            limit: Maximum number of results per page
            after: Cursor for pagination
            properties: List of properties to retrieve
            associations: List of associated object types
            archived: Whether to return archived calls

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if limit is not None:
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/calls"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_call(
        self,
        call_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get a single call by ID

        Args:
            call_id: Call ID
            properties: List of properties to retrieve
            associations: List of associated object types

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))

        url = self.base_url + "/crm/v3/objects/calls/{call_id}".format(call_id=call_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_call(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new call

        Args:
            properties: Call properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/calls"

        body = {}
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_call(
        self,
        call_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a call

        Args:
            call_id: Call ID
            properties: Call properties to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/calls/{call_id}".format(call_id=call_id)

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_call(
        self,
        call_id: str
    ) -> HubSpotResponse:
        """Delete a call

        Args:
            call_id: Call ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/calls/{call_id}".format(call_id=call_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_calls(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple calls

        Args:
            inputs: List of call objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/calls/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_calls(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple calls

        Args:
            inputs: List of call objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/calls/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_calls(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple calls

        Args:
            inputs: List of call identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/calls/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_email_activities(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """List email activities with filtering and pagination

        Args:
            limit: Maximum number of results per page
            after: Cursor for pagination
            properties: List of properties to retrieve
            associations: List of associated object types
            archived: Whether to return archived emails

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if limit is not None:
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/objects/emails"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_email_activity(
        self,
        email_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get a single email activity by ID

        Args:
            email_id: Email activity ID
            properties: List of properties to retrieve
            associations: List of associated object types

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))
        if associations is not None:
            for item in associations:
                query_params.append(('associations', item))

        url = self.base_url + "/crm/v3/objects/emails/{email_id}".format(email_id=email_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_email_activity(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new email activity

        Args:
            properties: Email activity properties
            associations: Associated objects

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/emails"

        body = {}
        body['properties'] = properties
        if associations is not None:
            body['associations'] = associations

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_email_activity(
        self,
        email_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update an email activity

        Args:
            email_id: Email activity ID
            properties: Email activity properties to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/emails/{email_id}".format(email_id=email_id)

        body = {}
        body['properties'] = properties

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_email_activity(
        self,
        email_id: str
    ) -> HubSpotResponse:
        """Delete an email activity

        Args:
            email_id: Email activity ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/emails/{email_id}".format(email_id=email_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_create_email_activities(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create multiple email activities

        Args:
            inputs: List of email activity objects to create

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/emails/batch/create"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_update_email_activities(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update multiple email activities

        Args:
            inputs: List of email activity objects to update

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/emails/batch/update"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def batch_delete_email_activities(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Delete multiple email activities

        Args:
            inputs: List of email activity identifiers to delete

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/objects/emails/batch/archive"

        body = {}
        body['inputs'] = inputs

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_marketing_emails(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        orderBy: Optional[str] = None
    ) -> HubSpotResponse:
        """List marketing emails

        Args:
            limit: Maximum number of results per page
            offset: Offset for pagination
            orderBy: Property to sort by

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if limit is not None:
            query_params.append(('limit', str(limit)))
        if offset is not None:
            query_params.append(('offset', str(offset)))
        if orderBy is not None:
            query_params.append(('orderBy', str(orderBy)))

        url = self.base_url + "/marketing/v3/marketing-emails"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_marketing_email(
        self,
        email_id: str
    ) -> HubSpotResponse:
        """Get marketing email by ID

        Args:
            email_id: Marketing email ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/marketing-emails/{email_id}".format(email_id=email_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_marketing_email(
        self,
        name: str,
        subject: str,
        html: str,
        from_name: str,
        from_email: str,
        reply_to: Optional[str] = None,
        template_id: Optional[str] = None
    ) -> HubSpotResponse:
        """Create a marketing email

        Args:
            name: Email name
            subject: Email subject
            html: Email HTML content
            from_name: From name
            from_email: From email
            reply_to: Reply to email
            template_id: Template ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/marketing-emails"

        body = {}
        body['name'] = name
        body['subject'] = subject
        body['html'] = html
        body['fromName'] = from_name
        body['fromEmail'] = from_email
        if reply_to is not None:
            body['replyTo'] = reply_to
        if template_id is not None:
            body['templateId'] = template_id

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_marketing_email(
        self,
        email_id: str,
        name: Optional[str] = None,
        subject: Optional[str] = None,
        html: Optional[str] = None
    ) -> HubSpotResponse:
        """Update a marketing email

        Args:
            email_id: Marketing email ID
            name: Email name
            subject: Email subject
            html: Email HTML content

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/marketing-emails/{email_id}".format(email_id=email_id)

        body = {}
        if name is not None:
            body['name'] = name
        if subject is not None:
            body['subject'] = subject
        if html is not None:
            body['html'] = html

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_marketing_email(
        self,
        email_id: str
    ) -> HubSpotResponse:
        """Delete a marketing email

        Args:
            email_id: Marketing email ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/marketing-emails/{email_id}".format(email_id=email_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def clone_marketing_email(
        self,
        email_id: str,
        name: str
    ) -> HubSpotResponse:
        """Clone a marketing email

        Args:
            email_id: Marketing email ID to clone
            name: Name for cloned email

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/marketing-emails/{email_id}/clone".format(email_id=email_id)

        body = {}
        body['name'] = name

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def publish_marketing_email(
        self,
        email_id: str
    ) -> HubSpotResponse:
        """Publish a marketing email

        Args:
            email_id: Marketing email ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/marketing-emails/{email_id}/publish".format(email_id=email_id)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def send_single_email(
        self,
        email_id: str,
        message: Dict[str, Any],
        contact_properties: Optional[Dict[str, Any]] = None,
        custom_properties: Optional[Dict[str, Any]] = None,
        send_id: Optional[str] = None
    ) -> HubSpotResponse:
        """Send single marketing email

        Args:
            email_id: Email ID to send
            message: Message details including recipient
            contact_properties: Contact properties for personalization
            custom_properties: Custom properties for personalization
            send_id: Unique send identifier

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v4/email/single-send"

        body = {}
        body['emailId'] = email_id
        body['message'] = message
        if contact_properties is not None:
            body['contactProperties'] = contact_properties
        if custom_properties is not None:
            body['customProperties'] = custom_properties
        if send_id is not None:
            body['sendId'] = send_id

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_pages(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        archived: Optional[bool] = None,
        created_at_gte: Optional[str] = None,
        created_at_lte: Optional[str] = None,
        updated_at_gte: Optional[str] = None,
        updated_at_lte: Optional[str] = None
    ) -> HubSpotResponse:
        """List CMS pages

        Args:
            limit: Maximum number of results
            after: Cursor for pagination
            archived: Include archived pages
            created_at_gte: Created after timestamp
            created_at_lte: Created before timestamp
            updated_at_gte: Updated after timestamp
            updated_at_lte: Updated before timestamp

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if limit is not None:
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))
        if created_at_gte is not None:
            query_params.append(('createdAtGte', str(created_at_gte)))
        if created_at_lte is not None:
            query_params.append(('createdAtLte', str(created_at_lte)))
        if updated_at_gte is not None:
            query_params.append(('updatedAtGte', str(updated_at_gte)))
        if updated_at_lte is not None:
            query_params.append(('updatedAtLte', str(updated_at_lte)))

        url = self.base_url + "/cms/v3/pages"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_page(
        self,
        page_id: str,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get CMS page by ID

        Args:
            page_id: Page ID
            archived: Include archived pages

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/cms/v3/pages/{page_id}".format(page_id=page_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_page(
        self,
        name: str,
        html_title: str,
        slug: str,
        content_group_id: str,
        template_path: str,
        html: Optional[str] = None,
        meta_description: Optional[str] = None,
        widgets: Optional[Dict[str, Any]] = None,
        state: Optional[str] = None
    ) -> HubSpotResponse:
        """Create a CMS page

        Args:
            name: Page name
            html_title: HTML title
            slug: Page slug/URL path
            content_group_id: Content group ID
            template_path: Template path
            html: Page HTML content
            meta_description: Meta description
            widgets: Page widgets
            state: Page state (DRAFT, PUBLISHED)

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/cms/v3/pages"

        body = {}
        body['name'] = name
        body['htmlTitle'] = html_title
        body['slug'] = slug
        body['contentGroupId'] = content_group_id
        body['templatePath'] = template_path
        if html is not None:
            body['html'] = html
        if meta_description is not None:
            body['metaDescription'] = meta_description
        if widgets is not None:
            body['widgets'] = widgets
        if state is not None:
            body['state'] = state

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_page(
        self,
        page_id: str,
        name: Optional[str] = None,
        html_title: Optional[str] = None,
        html: Optional[str] = None,
        meta_description: Optional[str] = None,
        state: Optional[str] = None
    ) -> HubSpotResponse:
        """Update a CMS page

        Args:
            page_id: Page ID
            name: Page name
            html_title: HTML title
            html: Page HTML content
            meta_description: Meta description
            state: Page state

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/cms/v3/pages/{page_id}".format(page_id=page_id)

        body = {}
        if name is not None:
            body['name'] = name
        if html_title is not None:
            body['htmlTitle'] = html_title
        if html is not None:
            body['html'] = html
        if meta_description is not None:
            body['metaDescription'] = meta_description
        if state is not None:
            body['state'] = state

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_page(
        self,
        page_id: str
    ) -> HubSpotResponse:
        """Delete a CMS page

        Args:
            page_id: Page ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/cms/v3/pages/{page_id}".format(page_id=page_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def publish_page(
        self,
        page_id: str
    ) -> HubSpotResponse:
        """Publish a CMS page

        Args:
            page_id: Page ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/cms/v3/pages/{page_id}/publish".format(page_id=page_id)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def unpublish_page(
        self,
        page_id: str
    ) -> HubSpotResponse:
        """Unpublish a CMS page

        Args:
            page_id: Page ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/cms/v3/pages/{page_id}/unpublish".format(page_id=page_id)

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def clone_page(
        self,
        page_id: str,
        name: str
    ) -> HubSpotResponse:
        """Clone a CMS page

        Args:
            page_id: Page ID to clone
            name: Name for cloned page

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/cms/v3/pages/{page_id}/clone".format(page_id=page_id)

        body = {}
        body['name'] = name

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_webhooks(
        self,
        app_id: str
    ) -> HubSpotResponse:
        """List webhook subscriptions

        Args:
            app_id: Application ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/webhooks/v3/{app_id}/subscriptions".format(app_id=app_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_webhook(
        self,
        app_id: str,
        event_type: str,
        webhook_url: str,
        property_name: Optional[str] = None,
        active: Optional[bool] = None
    ) -> HubSpotResponse:
        """Create webhook subscription

        Args:
            app_id: Application ID
            event_type: Event type to subscribe to
            webhook_url: Webhook endpoint URL
            property_name: Property name for property change events
            active: Whether subscription is active

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/webhooks/v3/{app_id}/subscriptions".format(app_id=app_id)

        body = {}
        body['eventType'] = event_type
        body['webhookUrl'] = webhook_url
        if property_name is not None:
            body['propertyName'] = property_name
        if active is not None:
            body['active'] = active

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_webhook(
        self,
        app_id: str,
        subscription_id: str
    ) -> HubSpotResponse:
        """Get webhook subscription

        Args:
            app_id: Application ID
            subscription_id: Subscription ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/webhooks/v3/{app_id}/subscriptions/{subscription_id}".format(app_id=app_id, subscription_id=subscription_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_webhook(
        self,
        app_id: str,
        subscription_id: str,
        webhook_url: Optional[str] = None,
        active: Optional[bool] = None
    ) -> HubSpotResponse:
        """Update webhook subscription

        Args:
            app_id: Application ID
            subscription_id: Subscription ID
            webhook_url: New webhook URL
            active: Whether subscription is active

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/webhooks/v3/{app_id}/subscriptions/{subscription_id}".format(app_id=app_id, subscription_id=subscription_id)

        body = {}
        if webhook_url is not None:
            body['webhookUrl'] = webhook_url
        if active is not None:
            body['active'] = active

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_webhook(
        self,
        app_id: str,
        subscription_id: str
    ) -> HubSpotResponse:
        """Delete webhook subscription

        Args:
            app_id: Application ID
            subscription_id: Subscription ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/webhooks/v3/{app_id}/subscriptions/{subscription_id}".format(app_id=app_id, subscription_id=subscription_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_properties(
        self,
        object_type: str,
        archived: Optional[bool] = None,
        properties: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """List properties for an object type

        Args:
            object_type: Object type (contacts, companies, deals, etc.)
            archived: Include archived properties
            properties: Specific properties to retrieve

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))
        if properties is not None:
            for item in properties:
                query_params.append(('properties', item))

        url = self.base_url + "/crm/v3/properties/{object_type}".format(object_type=object_type)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_property(
        self,
        object_type: str,
        property_name: str,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get property by name

        Args:
            object_type: Object type
            property_name: Property name
            archived: Include archived properties

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/crm/v3/properties/{object_type}/{property_name}".format(object_type=object_type, property_name=property_name)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_property(
        self,
        object_type: str,
        name: str,
        label: str,
        type: str,
        field_type: str,
        group_name: str,
        description: Optional[str] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        display_order: Optional[int] = None,
        has_unique_value: Optional[bool] = None,
        hidden: Optional[bool] = None,
        form_field: Optional[bool] = None
    ) -> HubSpotResponse:
        """Create a new property

        Args:
            object_type: Object type
            name: Property name
            label: Property label
            type: Property type (string, number, datetime, enumeration, etc.)
            field_type: Field type (text, textarea, select, etc.)
            group_name: Property group name
            description: Property description
            options: Options for enumeration properties
            display_order: Display order
            has_unique_value: Whether property values must be unique
            hidden: Whether property is hidden
            form_field: Whether property can be used in forms

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/properties/{object_type}".format(object_type=object_type)

        body = {}
        body['name'] = name
        body['label'] = label
        body['type'] = type
        body['fieldType'] = field_type
        body['groupName'] = group_name
        if description is not None:
            body['description'] = description
        if options is not None:
            body['options'] = options
        if display_order is not None:
            body['displayOrder'] = display_order
        if has_unique_value is not None:
            body['hasUniqueValue'] = has_unique_value
        if hidden is not None:
            body['hidden'] = hidden
        if form_field is not None:
            body['formField'] = form_field

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_property(
        self,
        object_type: str,
        property_name: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        hidden: Optional[bool] = None,
        form_field: Optional[bool] = None
    ) -> HubSpotResponse:
        """Update a property

        Args:
            object_type: Object type
            property_name: Property name
            label: Property label
            description: Property description
            options: Options for enumeration properties
            hidden: Whether property is hidden
            form_field: Whether property can be used in forms

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/properties/{object_type}/{property_name}".format(object_type=object_type, property_name=property_name)

        body = {}
        if label is not None:
            body['label'] = label
        if description is not None:
            body['description'] = description
        if options is not None:
            body['options'] = options
        if hidden is not None:
            body['hidden'] = hidden
        if form_field is not None:
            body['formField'] = form_field

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_property(
        self,
        object_type: str,
        property_name: str
    ) -> HubSpotResponse:
        """Delete a property

        Args:
            object_type: Object type
            property_name: Property name

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/crm/v3/properties/{object_type}/{property_name}".format(object_type=object_type, property_name=property_name)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def list_forms(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """List forms

        Args:
            limit: Maximum number of results
            after: Cursor for pagination
            archived: Include archived forms

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if limit is not None:
            query_params.append(('limit', str(limit)))
        if after is not None:
            query_params.append(('after', str(after)))
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/marketing/v3/forms"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def get_form(
        self,
        form_id: str,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get form by ID

        Args:
            form_id: Form ID
            archived: Include archived forms

        Returns:
            HubSpotResponse with operation result
        """
        query_params = []
        if archived is not None:
            query_params.append(('archived', 'true' if archived else 'false'))

        url = self.base_url + "/marketing/v3/forms/{form_id}".format(form_id=form_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def create_form(
        self,
        name: str,
        form_fields: List[Dict[str, Any]],
        submit_text: str,
        notification_recipients: Optional[List[str]] = None,
        redirect: Optional[str] = None,
        thank_you_message_html: Optional[str] = None,
        captcha_enabled: Optional[bool] = None,
        create_new_contact_for_new_email: Optional[bool] = None,
        pre_populate_known_values: Optional[bool] = None,
        allow_link_to_reset_known_values: Optional[bool] = None,
        embed_code: Optional[str] = None,
        cloneable: Optional[bool] = None,
        editable: Optional[bool] = None,
        deletable: Optional[bool] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Create a new form

        Args:
            name: Form name
            form_fields: Form field definitions
            submit_text: Submit button text
            notification_recipients: Email notification recipients
            redirect: Redirect URL after form submission
            thank_you_message_html: Thank you message HTML
            captcha_enabled: Enable CAPTCHA
            create_new_contact_for_new_email: Create new contact for new emails
            pre_populate_known_values: Pre-populate known contact values
            allow_link_to_reset_known_values: Allow link to reset known values
            embed_code: Form embed code
            cloneable: Whether form can be cloned
            editable: Whether form is editable
            deletable: Whether form can be deleted
            archived: Whether form is archived

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/forms"

        body = {}
        body['name'] = name
        body['formFields'] = form_fields
        body['submitText'] = submit_text
        if notification_recipients is not None:
            body['notificationRecipients'] = notification_recipients
        if redirect is not None:
            body['redirect'] = redirect
        if thank_you_message_html is not None:
            body['thankYouMessageHtml'] = thank_you_message_html
        if captcha_enabled is not None:
            body['captchaEnabled'] = captcha_enabled
        if create_new_contact_for_new_email is not None:
            body['createNewContactForNewEmail'] = create_new_contact_for_new_email
        if pre_populate_known_values is not None:
            body['prePopulateKnownValues'] = pre_populate_known_values
        if allow_link_to_reset_known_values is not None:
            body['allowLinkToResetKnownValues'] = allow_link_to_reset_known_values
        if embed_code is not None:
            body['embedCode'] = embed_code
        if cloneable is not None:
            body['cloneable'] = cloneable
        if editable is not None:
            body['editable'] = editable
        if deletable is not None:
            body['deletable'] = deletable
        if archived is not None:
            body['archived'] = archived

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def update_form(
        self,
        form_id: str,
        name: Optional[str] = None,
        submit_text: Optional[str] = None,
        redirect: Optional[str] = None,
        thank_you_message_html: Optional[str] = None
    ) -> HubSpotResponse:
        """Update a form

        Args:
            form_id: Form ID
            name: Form name
            submit_text: Submit button text
            redirect: Redirect URL after form submission
            thank_you_message_html: Thank you message HTML

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/forms/{form_id}".format(form_id=form_id)

        body = {}
        if name is not None:
            body['name'] = name
        if submit_text is not None:
            body['submitText'] = submit_text
        if redirect is not None:
            body['redirect'] = redirect
        if thank_you_message_html is not None:
            body['thankYouMessageHtml'] = thank_you_message_html

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    async def delete_form(
        self,
        form_id: str
    ) -> HubSpotResponse:
        """Delete a form

        Args:
            form_id: Form ID

        Returns:
            HubSpotResponse with operation result
        """
        url = self.base_url + "/marketing/v3/forms/{form_id}".format(form_id=form_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return HubSpotResponse(success=True, data=response.json())
        except Exception as e:
            return HubSpotResponse(success=False, error=str(e))

    def get_client_info(self) -> HubSpotResponse:
        """Get information about the HubSpot client."""
        info = {
            'total_methods': 108,
            'base_url': self.base_url,
            'api_categories': [
                'CRM APIs (Contacts, Companies, Deals, Tickets, etc.)',
                'Activities APIs (Calls, Emails, Meetings, Notes, Tasks)',
                'Marketing APIs (Emails, Campaigns, Forms, Events)',
                'Automation APIs (Workflows, Actions, Sequences)',
                'CMS APIs (Pages, Blog Posts, HubDB, Domains)',
                'Conversations APIs (Messages, Visitor Identification)',
                'Events APIs (Custom Events, Event Definitions)',
                'Settings APIs (Business Units, User Provisioning)',
                'Integration APIs (Webhooks, CRM Extensions)',
                'Communication Preferences APIs (Subscriptions)',
                'Files APIs (File Management)',
                'OAuth & Authentication APIs',
                'Meetings APIs (Scheduler)'
            ]
        }
        return HubSpotResponse(success=True, data=info)
