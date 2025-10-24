from typing import Dict, List, Optional, Union, Literal
import json
from urllib.parse import urlencode
from dataclasses import asdict
from datetime import datetime

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.hubspot.hubspot import HubSpotClient


class HubSpotResponse:
    """Standardized HubSpot API response wrapper."""
    
    def __init__(self, success: bool, data=None, error: str = None, message: str = None):
        self.success = success
        self.data = data
        self.error = error
        self.message = message
    
    def to_dict(self) -> Dict[str, any]:
        """Convert response to dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "message": self.message
        }
    
    def __repr__(self) -> str:
        return f"HubSpotResponse(success={self.success}, error={self.error})"


class HubSpotDataSource:
    """Auto-generated HubSpot API client wrapper.
    
    Provides async methods for ALL HubSpot API endpoints:
    - CRM API (Contacts, Companies, Deals, Tickets, Properties, Associations)
    - CMS API (Pages, Blog Posts, HubDB, Authors, Tags)
    - Marketing API (Campaigns, Email, Social)
    - Conversations API (Messages, Threads, Channels)
    - Automation API (Workflows, Sequences)
    - Events API (Custom Events, Behavioral Events)
    - Commerce API (Payments, Subscriptions)
    - Webhooks API (Subscriptions, Events)
    - Account API (Info, Activity, Usage)
    - Settings API (Users, Teams, Business Units)
    
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
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """List all contacts with optional filtering and sorting

        Args:
            limit: Number of results to return (max 100)
            after: Pagination cursor
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for
            archived: Whether to return only archived results

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list contacts")

    async def get_contact(
        self,
        contact_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get a contact by ID

        Args:
            contact_id: Contact ID
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for
            archived: Whether to return archived results

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get contact")

    async def create_contact(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new contact

        Args:
            properties: Contact property values
            associations: List of objects to associate with this contact

        Returns:
            HubSpotResponse with operation result
        """
        try:
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create contact")

    async def update_contact(
        self,
        contact_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a contact

        Args:
            contact_id: Contact ID
            properties: Contact property values to update

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/contacts/{contact_id}".format(contact_id=contact_id)

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update contact")

    async def delete_contact(
        self,
        contact_id: str
    ) -> HubSpotResponse:
        """Archive (delete) a contact

        Args:
            contact_id: Contact ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/contacts/{contact_id}".format(contact_id=contact_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete contact")

    async def batch_create_contacts(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create a batch of contacts

        Args:
            inputs: Array of contact objects to create

        Returns:
            HubSpotResponse with operation result
        """
        try:
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute batch create contacts")

    async def batch_read_contacts(
        self,
        inputs: List[Dict[str, str]],
        properties: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Read a batch of contacts by ID

        Args:
            inputs: Array of contact IDs to read
            properties: List of properties to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/contacts/batch/read"

            body = {}
            body['inputs'] = inputs
            if properties is not None:
                body['properties'] = properties

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute batch read contacts")

    async def batch_update_contacts(
        self,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Update a batch of contacts

        Args:
            inputs: Array of contact objects to update

        Returns:
            HubSpotResponse with operation result
        """
        try:
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute batch update contacts")

    async def batch_archive_contacts(
        self,
        inputs: List[Dict[str, str]]
    ) -> HubSpotResponse:
        """Archive a batch of contacts

        Args:
            inputs: Array of contact IDs to archive

        Returns:
            HubSpotResponse with operation result
        """
        try:
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute batch archive contacts")

    async def search_contacts(
        self,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        sorts: Optional[List[Dict[str, str]]] = None,
        properties: Optional[List[str]] = None,
        filter_groups: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Search contacts

        Args:
            query: Search query
            limit: Number of results to return
            after: Pagination cursor
            sorts: Sort configuration
            properties: Properties to return
            filter_groups: Filter groups for search

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/contacts/search"

            body = {}
            if query is not None:
                body['query'] = query
            if limit is not None:
                body['limit'] = limit
            if after is not None:
                body['after'] = after
            if sorts is not None:
                body['sorts'] = sorts
            if properties is not None:
                body['properties'] = properties
            if filter_groups is not None:
                body['filter_groups'] = filter_groups

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute search contacts")

    async def list_companies(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """List all companies

        Args:
            limit: Number of results to return (max 100)
            after: Pagination cursor
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for
            archived: Whether to return only archived results

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list companies")

    async def get_company(
        self,
        company_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get a company by ID

        Args:
            company_id: Company ID
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get company")

    async def create_company(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new company

        Args:
            properties: Company property values
            associations: List of objects to associate with this company

        Returns:
            HubSpotResponse with operation result
        """
        try:
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create company")

    async def update_company(
        self,
        company_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a company

        Args:
            company_id: Company ID
            properties: Company property values to update

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/companies/{company_id}".format(company_id=company_id)

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update company")

    async def delete_company(
        self,
        company_id: str
    ) -> HubSpotResponse:
        """Archive (delete) a company

        Args:
            company_id: Company ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/companies/{company_id}".format(company_id=company_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete company")

    async def search_companies(
        self,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        sorts: Optional[List[Dict[str, str]]] = None,
        properties: Optional[List[str]] = None,
        filter_groups: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Search companies

        Args:
            query: Search query
            limit: Number of results to return
            after: Pagination cursor
            sorts: Sort configuration
            properties: Properties to return
            filter_groups: Filter groups for search

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/companies/search"

            body = {}
            if query is not None:
                body['query'] = query
            if limit is not None:
                body['limit'] = limit
            if after is not None:
                body['after'] = after
            if sorts is not None:
                body['sorts'] = sorts
            if properties is not None:
                body['properties'] = properties
            if filter_groups is not None:
                body['filter_groups'] = filter_groups

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute search companies")

    async def list_deals(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """List all deals

        Args:
            limit: Number of results to return (max 100)
            after: Pagination cursor
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list deals")

    async def get_deal(
        self,
        deal_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get a deal by ID

        Args:
            deal_id: Deal ID
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get deal")

    async def create_deal(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new deal

        Args:
            properties: Deal property values
            associations: List of objects to associate with this deal

        Returns:
            HubSpotResponse with operation result
        """
        try:
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create deal")

    async def update_deal(
        self,
        deal_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a deal

        Args:
            deal_id: Deal ID
            properties: Deal property values to update

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/deals/{deal_id}".format(deal_id=deal_id)

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update deal")

    async def delete_deal(
        self,
        deal_id: str
    ) -> HubSpotResponse:
        """Archive (delete) a deal

        Args:
            deal_id: Deal ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/deals/{deal_id}".format(deal_id=deal_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete deal")

    async def search_deals(
        self,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        sorts: Optional[List[Dict[str, str]]] = None,
        properties: Optional[List[str]] = None,
        filter_groups: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Search deals

        Args:
            query: Search query
            limit: Number of results to return
            after: Pagination cursor
            sorts: Sort configuration
            properties: Properties to return
            filter_groups: Filter groups for search

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/deals/search"

            body = {}
            if query is not None:
                body['query'] = query
            if limit is not None:
                body['limit'] = limit
            if after is not None:
                body['after'] = after
            if sorts is not None:
                body['sorts'] = sorts
            if properties is not None:
                body['properties'] = properties
            if filter_groups is not None:
                body['filter_groups'] = filter_groups

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute search deals")

    async def list_tickets(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """List all tickets

        Args:
            limit: Number of results to return (max 100)
            after: Pagination cursor
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list tickets")

    async def get_ticket(
        self,
        ticket_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get a ticket by ID

        Args:
            ticket_id: Ticket ID
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get ticket")

    async def create_ticket(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new ticket

        Args:
            properties: Ticket property values
            associations: List of objects to associate with this ticket

        Returns:
            HubSpotResponse with operation result
        """
        try:
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create ticket")

    async def update_ticket(
        self,
        ticket_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a ticket

        Args:
            ticket_id: Ticket ID
            properties: Ticket property values to update

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/tickets/{ticket_id}".format(ticket_id=ticket_id)

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update ticket")

    async def delete_ticket(
        self,
        ticket_id: str
    ) -> HubSpotResponse:
        """Archive (delete) a ticket

        Args:
            ticket_id: Ticket ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/tickets/{ticket_id}".format(ticket_id=ticket_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete ticket")

    async def get_contact_properties(
        self,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get all contact properties

        Args:
            archived: Whether to return only archived properties

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/crm/v3/properties/contacts"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get contact properties")

    async def create_contact_property(
        self,
        name: str,
        label: str,
        type: str,
        field_type: str,
        group_name: Optional[str] = None,
        description: Optional[str] = None,
        options: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new contact property

        Args:
            name: Internal property name
            label: Human-readable property label
            type: Property data type
            field_type: Property field type
            group_name: Property group name
            description: Property description
            options: Enumeration options

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/properties/contacts"

            body = {}
            body['name'] = name
            body['label'] = label
            body['type'] = type
            body['field_type'] = field_type
            if group_name is not None:
                body['group_name'] = group_name
            if description is not None:
                body['description'] = description
            if options is not None:
                body['options'] = options

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create contact property")

    async def get_company_properties(
        self,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get all company properties

        Args:
            archived: Whether to return only archived properties

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/crm/v3/properties/companies"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get company properties")

    async def create_company_property(
        self,
        name: str,
        label: str,
        type: str,
        field_type: str,
        group_name: Optional[str] = None,
        description: Optional[str] = None,
        options: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new company property

        Args:
            name: Internal property name
            label: Human-readable property label
            type: Property data type
            field_type: Property field type
            group_name: Property group name
            description: Property description
            options: Enumeration options

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/properties/companies"

            body = {}
            body['name'] = name
            body['label'] = label
            body['type'] = type
            body['field_type'] = field_type
            if group_name is not None:
                body['group_name'] = group_name
            if description is not None:
                body['description'] = description
            if options is not None:
                body['options'] = options

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create company property")

    async def get_deal_properties(
        self,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get all deal properties

        Args:
            archived: Whether to return only archived properties

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/crm/v3/properties/deals"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get deal properties")

    async def get_ticket_properties(
        self,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get all ticket properties

        Args:
            archived: Whether to return only archived properties

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/crm/v3/properties/tickets"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get ticket properties")

    async def get_associations(
        self,
        from_object_type: str,
        object_id: str,
        to_object_type: str,
        after: Optional[str] = None,
        limit: Optional[int] = None
    ) -> HubSpotResponse:
        """Get associations for an object

        Args:
            from_object_type: Source object type
            object_id: Source object ID
            to_object_type: Target object type
            after: Pagination cursor
            limit: Number of results to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if after is not None:
                query_params.append(('after', str(after)))
            if limit is not None:
                query_params.append(('limit', str(limit)))

            url = self.base_url + "/crm/v4/objects/{from_object_type}/{object_id}/associations/{to_object_type}".format(from_object_type=from_object_type, object_id=object_id, to_object_type=to_object_type)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get associations")

    async def create_association(
        self,
        from_object_type: str,
        from_object_id: str,
        to_object_type: str,
        to_object_id: str,
        association_category: Optional[str] = None,
        association_type_id: Optional[int] = None
    ) -> HubSpotResponse:
        """Create an association between two objects

        Args:
            from_object_type: Source object type
            from_object_id: Source object ID
            to_object_type: Target object type
            to_object_id: Target object ID
            association_category: Association category
            association_type_id: Association type ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v4/objects/{from_object_type}/{from_object_id}/associations/{to_object_type}/{to_object_id}".format(from_object_type=from_object_type, from_object_id=from_object_id, to_object_type=to_object_type, to_object_id=to_object_id)

            body = {}
            if association_category is not None:
                body['association_category'] = association_category
            if association_type_id is not None:
                body['association_type_id'] = association_type_id

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="PUT",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create association")

    async def delete_association(
        self,
        from_object_type: str,
        from_object_id: str,
        to_object_type: str,
        to_object_id: str
    ) -> HubSpotResponse:
        """Remove an association between two objects

        Args:
            from_object_type: Source object type
            from_object_id: Source object ID
            to_object_type: Target object type
            to_object_id: Target object ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v4/objects/{from_object_type}/{from_object_id}/associations/{to_object_type}/{to_object_id}".format(from_object_type=from_object_type, from_object_id=from_object_id, to_object_type=to_object_type, to_object_id=to_object_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete association")

    async def batch_create_associations(
        self,
        from_object_type: str,
        to_object_type: str,
        inputs: List[Dict[str, Any]]
    ) -> HubSpotResponse:
        """Create a batch of associations

        Args:
            from_object_type: Source object type
            to_object_type: Target object type
            inputs: Array of association objects to create

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v4/associations/{from_object_type}/{to_object_type}/batch/create".format(from_object_type=from_object_type, to_object_type=to_object_type)

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute batch create associations")

    async def list_pages(
        self,
        created_at: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_at: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None,
        sort: Optional[List[str]] = None,
        after: Optional[str] = None,
        limit: Optional[int] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get all website pages

        Args:
            created_at: Filter by creation date
            created_after: Filter pages created after date
            created_before: Filter pages created before date
            updated_at: Filter by update date
            updated_after: Filter pages updated after date
            updated_before: Filter pages updated before date
            sort: Sort order
            after: Pagination cursor
            limit: Number of results to return
            archived: Return archived pages

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if created_at is not None:
                query_params.append(('created_at', str(created_at)))
            if created_after is not None:
                query_params.append(('createdAfter', str(created_after)))
            if created_before is not None:
                query_params.append(('createdBefore', str(created_before)))
            if updated_at is not None:
                query_params.append(('updated_at', str(updated_at)))
            if updated_after is not None:
                query_params.append(('updatedAfter', str(updated_after)))
            if updated_before is not None:
                query_params.append(('updatedBefore', str(updated_before)))
            if sort is not None:
                for item in sort:
                    query_params.append(('sort', item))
            if after is not None:
                query_params.append(('after', str(after)))
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/cms/v3/pages/site-pages"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list pages")

    async def get_page(
        self,
        page_id: str,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get a website page by ID

        Args:
            page_id: Page ID
            archived: Return archived page

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/cms/v3/pages/site-pages/{page_id}".format(page_id=page_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get page")

    async def create_page(
        self,
        slug: str,
        name: str,
        html_title: Optional[str] = None,
        meta_description: Optional[str] = None,
        language: Optional[str] = None,
        layout_sections: Optional[Dict[str, Any]] = None,
        state: Optional[Literal["DRAFT", "PUBLISHED"]] = None
    ) -> HubSpotResponse:
        """Create a new website page

        Args:
            slug: Page slug/URL
            name: Page name
            html_title: HTML title
            meta_description: Meta description
            language: Page language
            layout_sections: Page layout and content
            state: Page state

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/pages/site-pages"

            body = {}
            body['slug'] = slug
            body['name'] = name
            if html_title is not None:
                body['html_title'] = html_title
            if meta_description is not None:
                body['meta_description'] = meta_description
            if language is not None:
                body['language'] = language
            if layout_sections is not None:
                body['layout_sections'] = layout_sections
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create page")

    async def update_page(
        self,
        page_id: str,
        slug: Optional[str] = None,
        name: Optional[str] = None,
        html_title: Optional[str] = None,
        meta_description: Optional[str] = None,
        language: Optional[str] = None,
        layout_sections: Optional[Dict[str, Any]] = None,
        state: Optional[Literal["DRAFT", "PUBLISHED"]] = None
    ) -> HubSpotResponse:
        """Update a website page

        Args:
            page_id: Page ID
            slug: Page slug/URL
            name: Page name
            html_title: HTML title
            meta_description: Meta description
            language: Page language
            layout_sections: Page layout and content
            state: Page state

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/pages/site-pages/{page_id}".format(page_id=page_id)

            body = {}
            if slug is not None:
                body['slug'] = slug
            if name is not None:
                body['name'] = name
            if html_title is not None:
                body['html_title'] = html_title
            if meta_description is not None:
                body['meta_description'] = meta_description
            if language is not None:
                body['language'] = language
            if layout_sections is not None:
                body['layout_sections'] = layout_sections
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update page")

    async def delete_page(
        self,
        page_id: str
    ) -> HubSpotResponse:
        """Archive a website page

        Args:
            page_id: Page ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/pages/site-pages/{page_id}".format(page_id=page_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete page")

    async def list_blog_posts(
        self,
        created_at: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_at: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None,
        sort: Optional[List[str]] = None,
        after: Optional[str] = None,
        limit: Optional[int] = None,
        archived: Optional[bool] = None,
        state: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all blog posts

        Args:
            created_at: Filter by creation date
            created_after: Filter posts created after date
            created_before: Filter posts created before date
            updated_at: Filter by update date
            updated_after: Filter posts updated after date
            updated_before: Filter posts updated before date
            sort: Sort order
            after: Pagination cursor
            limit: Number of results to return
            archived: Return archived posts
            state: Filter by publish state

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if created_at is not None:
                query_params.append(('created_at', str(created_at)))
            if created_after is not None:
                query_params.append(('createdAfter', str(created_after)))
            if created_before is not None:
                query_params.append(('createdBefore', str(created_before)))
            if updated_at is not None:
                query_params.append(('updated_at', str(updated_at)))
            if updated_after is not None:
                query_params.append(('updatedAfter', str(updated_after)))
            if updated_before is not None:
                query_params.append(('updatedBefore', str(updated_before)))
            if sort is not None:
                for item in sort:
                    query_params.append(('sort', item))
            if after is not None:
                query_params.append(('after', str(after)))
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))
            if state is not None:
                query_params.append(('state', str(state)))

            url = self.base_url + "/cms/v3/blogs/posts"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list blog posts")

    async def get_blog_post(
        self,
        post_id: str,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get a blog post by ID

        Args:
            post_id: Blog post ID
            archived: Return archived post

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/cms/v3/blogs/posts/{post_id}".format(post_id=post_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get blog post")

    async def create_blog_post(
        self,
        slug: str,
        name: str,
        html_title: Optional[str] = None,
        meta_description: Optional[str] = None,
        post_body: Optional[str] = None,
        post_summary: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        blog_author_id: Optional[str] = None,
        featured_image: Optional[str] = None,
        state: Optional[Literal["DRAFT", "PUBLISHED", "SCHEDULED"]] = None
    ) -> HubSpotResponse:
        """Create a new blog post

        Args:
            slug: Post slug/URL
            name: Post title
            html_title: HTML title
            meta_description: Meta description
            post_body: Post content HTML
            post_summary: Post summary
            tag_ids: List of tag IDs
            blog_author_id: Author ID
            featured_image: Featured image URL
            state: Post state

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/blogs/posts"

            body = {}
            body['slug'] = slug
            body['name'] = name
            if html_title is not None:
                body['html_title'] = html_title
            if meta_description is not None:
                body['meta_description'] = meta_description
            if post_body is not None:
                body['post_body'] = post_body
            if post_summary is not None:
                body['post_summary'] = post_summary
            if tag_ids is not None:
                body['tag_ids'] = tag_ids
            if blog_author_id is not None:
                body['blog_author_id'] = blog_author_id
            if featured_image is not None:
                body['featured_image'] = featured_image
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create blog post")

    async def update_blog_post(
        self,
        post_id: str,
        slug: Optional[str] = None,
        name: Optional[str] = None,
        html_title: Optional[str] = None,
        meta_description: Optional[str] = None,
        post_body: Optional[str] = None,
        post_summary: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        blog_author_id: Optional[str] = None,
        featured_image: Optional[str] = None,
        state: Optional[Literal["DRAFT", "PUBLISHED", "SCHEDULED"]] = None
    ) -> HubSpotResponse:
        """Update a blog post

        Args:
            post_id: Blog post ID
            slug: Post slug/URL
            name: Post title
            html_title: HTML title
            meta_description: Meta description
            post_body: Post content HTML
            post_summary: Post summary
            tag_ids: List of tag IDs
            blog_author_id: Author ID
            featured_image: Featured image URL
            state: Post state

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/blogs/posts/{post_id}".format(post_id=post_id)

            body = {}
            if slug is not None:
                body['slug'] = slug
            if name is not None:
                body['name'] = name
            if html_title is not None:
                body['html_title'] = html_title
            if meta_description is not None:
                body['meta_description'] = meta_description
            if post_body is not None:
                body['post_body'] = post_body
            if post_summary is not None:
                body['post_summary'] = post_summary
            if tag_ids is not None:
                body['tag_ids'] = tag_ids
            if blog_author_id is not None:
                body['blog_author_id'] = blog_author_id
            if featured_image is not None:
                body['featured_image'] = featured_image
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update blog post")

    async def delete_blog_post(
        self,
        post_id: str
    ) -> HubSpotResponse:
        """Archive a blog post

        Args:
            post_id: Blog post ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/blogs/posts/{post_id}".format(post_id=post_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete blog post")

    async def list_blog_authors(
        self,
        created_at: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_at: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None,
        sort: Optional[List[str]] = None,
        after: Optional[str] = None,
        limit: Optional[int] = None
    ) -> HubSpotResponse:
        """Get all blog authors

        Args:
            created_at: Filter by creation date
            created_after: Filter authors created after date
            created_before: Filter authors created before date
            updated_at: Filter by update date
            updated_after: Filter authors updated after date
            updated_before: Filter authors updated before date
            sort: Sort order
            after: Pagination cursor
            limit: Number of results to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if created_at is not None:
                query_params.append(('created_at', str(created_at)))
            if created_after is not None:
                query_params.append(('createdAfter', str(created_after)))
            if created_before is not None:
                query_params.append(('createdBefore', str(created_before)))
            if updated_at is not None:
                query_params.append(('updated_at', str(updated_at)))
            if updated_after is not None:
                query_params.append(('updatedAfter', str(updated_after)))
            if updated_before is not None:
                query_params.append(('updatedBefore', str(updated_before)))
            if sort is not None:
                for item in sort:
                    query_params.append(('sort', item))
            if after is not None:
                query_params.append(('after', str(after)))
            if limit is not None:
                query_params.append(('limit', str(limit)))

            url = self.base_url + "/cms/v3/blogs/authors"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list blog authors")

    async def list_blog_tags(
        self,
        created_at: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_at: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None,
        sort: Optional[List[str]] = None,
        after: Optional[str] = None,
        limit: Optional[int] = None
    ) -> HubSpotResponse:
        """Get all blog tags

        Args:
            created_at: Filter by creation date
            created_after: Filter tags created after date
            created_before: Filter tags created before date
            updated_at: Filter by update date
            updated_after: Filter tags updated after date
            updated_before: Filter tags updated before date
            sort: Sort order
            after: Pagination cursor
            limit: Number of results to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if created_at is not None:
                query_params.append(('created_at', str(created_at)))
            if created_after is not None:
                query_params.append(('createdAfter', str(created_after)))
            if created_before is not None:
                query_params.append(('createdBefore', str(created_before)))
            if updated_at is not None:
                query_params.append(('updated_at', str(updated_at)))
            if updated_after is not None:
                query_params.append(('updatedAfter', str(updated_after)))
            if updated_before is not None:
                query_params.append(('updatedBefore', str(updated_before)))
            if sort is not None:
                for item in sort:
                    query_params.append(('sort', item))
            if after is not None:
                query_params.append(('after', str(after)))
            if limit is not None:
                query_params.append(('limit', str(limit)))

            url = self.base_url + "/cms/v3/blogs/tags"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list blog tags")

    async def list_hubdb_tables(
        self,
        sort: Optional[List[str]] = None,
        after: Optional[str] = None,
        limit: Optional[int] = None,
        archived: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get all HubDB tables

        Args:
            sort: Sort order
            after: Pagination cursor
            limit: Number of results to return
            archived: Return archived tables

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if sort is not None:
                for item in sort:
                    query_params.append(('sort', item))
            if after is not None:
                query_params.append(('after', str(after)))
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))

            url = self.base_url + "/cms/v3/hubdb/tables"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list hubdb tables")

    async def get_hubdb_table(
        self,
        table_id: str,
        archived: Optional[bool] = None,
        include_foreign_ids: Optional[bool] = None
    ) -> HubSpotResponse:
        """Get a HubDB table by ID

        Args:
            table_id: Table ID
            archived: Return archived table
            include_foreign_ids: Include foreign key IDs

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if archived is not None:
                query_params.append(('archived', 'true' if archived else 'false'))
            if include_foreign_ids is not None:
                query_params.append(('include_foreign_ids', 'true' if include_foreign_ids else 'false'))

            url = self.base_url + "/cms/v3/hubdb/tables/{table_id}".format(table_id=table_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get hubdb table")

    async def create_hubdb_table(
        self,
        name: str,
        label: str,
        columns: List[Dict[str, Any]],
        allow_public_api_access: Optional[bool] = None,
        use_for_pages: Optional[bool] = None,
        dynamic_meta_tags: Optional[Dict[str, Any]] = None
    ) -> HubSpotResponse:
        """Create a new HubDB table

        Args:
            name: Table name
            label: Table label
            columns: Table columns
            allow_public_api_access: Allow public API access
            use_for_pages: Use table for dynamic pages
            dynamic_meta_tags: Dynamic meta tags configuration

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/hubdb/tables"

            body = {}
            body['name'] = name
            body['label'] = label
            body['columns'] = columns
            if allow_public_api_access is not None:
                body['allow_public_api_access'] = allow_public_api_access
            if use_for_pages is not None:
                body['use_for_pages'] = use_for_pages
            if dynamic_meta_tags is not None:
                body['dynamic_meta_tags'] = dynamic_meta_tags

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create hubdb table")

    async def update_hubdb_table(
        self,
        table_id: str,
        name: Optional[str] = None,
        label: Optional[str] = None,
        allow_public_api_access: Optional[bool] = None,
        use_for_pages: Optional[bool] = None,
        dynamic_meta_tags: Optional[Dict[str, Any]] = None
    ) -> HubSpotResponse:
        """Update a HubDB table

        Args:
            table_id: Table ID
            name: Table name
            label: Table label
            allow_public_api_access: Allow public API access
            use_for_pages: Use table for dynamic pages
            dynamic_meta_tags: Dynamic meta tags configuration

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/hubdb/tables/{table_id}".format(table_id=table_id)

            body = {}
            if name is not None:
                body['name'] = name
            if label is not None:
                body['label'] = label
            if allow_public_api_access is not None:
                body['allow_public_api_access'] = allow_public_api_access
            if use_for_pages is not None:
                body['use_for_pages'] = use_for_pages
            if dynamic_meta_tags is not None:
                body['dynamic_meta_tags'] = dynamic_meta_tags

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="PATCH",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update hubdb table")

    async def delete_hubdb_table(
        self,
        table_id: str
    ) -> HubSpotResponse:
        """Archive a HubDB table

        Args:
            table_id: Table ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/hubdb/tables/{table_id}".format(table_id=table_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete hubdb table")

    async def list_hubdb_table_rows(
        self,
        table_id: str,
        sort: Optional[List[str]] = None,
        after: Optional[str] = None,
        limit: Optional[int] = None
    ) -> HubSpotResponse:
        """Get all rows from a HubDB table

        Args:
            table_id: Table ID
            sort: Sort order
            after: Pagination cursor
            limit: Number of results to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if sort is not None:
                for item in sort:
                    query_params.append(('sort', item))
            if after is not None:
                query_params.append(('after', str(after)))
            if limit is not None:
                query_params.append(('limit', str(limit)))

            url = self.base_url + "/cms/v3/hubdb/tables/{table_id}/rows".format(table_id=table_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list hubdb table rows")

    async def create_hubdb_table_row(
        self,
        table_id: str,
        values: Dict[str, Any]
    ) -> HubSpotResponse:
        """Create a new row in a HubDB table

        Args:
            table_id: Table ID
            values: Row data values

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/cms/v3/hubdb/tables/{table_id}/rows".format(table_id=table_id)

            body = {}
            body['values'] = values

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create hubdb table row")

    async def list_campaigns(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all campaigns

        Args:
            limit: Number of results to return
            after: Pagination cursor
            properties: Comma-separated list of properties to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))
            if properties is not None:
                query_params.append(('properties', str(properties)))

            url = self.base_url + "/marketing/v3/campaigns"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list campaigns")

    async def get_campaign(
        self,
        campaign_id: str,
        properties: Optional[str] = None
    ) -> HubSpotResponse:
        """Get a campaign by ID

        Args:
            campaign_id: Campaign ID
            properties: Comma-separated list of properties to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if properties is not None:
                query_params.append(('properties', str(properties)))

            url = self.base_url + "/marketing/v3/campaigns/{campaign_id}".format(campaign_id=campaign_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get campaign")

    async def create_campaign(
        self,
        hs_name: str,
        hs_start_date: Optional[str] = None,
        hs_end_date: Optional[str] = None,
        hs_notes: Optional[str] = None,
        hs_audience: Optional[str] = None,
        hs_currency_code: Optional[str] = None,
        hs_campaign_status: Optional[Literal["planned", "in_progress", "active", "paused", "completed"]] = None,
        hs_utm: Optional[str] = None
    ) -> HubSpotResponse:
        """Create a new campaign

        Args:
            hs_name: Campaign name
            hs_start_date: Campaign start date (YYYY-MM-DD)
            hs_end_date: Campaign end date (YYYY-MM-DD)
            hs_notes: Campaign notes
            hs_audience: Campaign audience
            hs_currency_code: Currency code
            hs_campaign_status: Campaign status
            hs_utm: UTM values

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/marketing/v3/campaigns"

            body = {}
            body['hs_name'] = hs_name
            if hs_start_date is not None:
                body['hs_start_date'] = hs_start_date
            if hs_end_date is not None:
                body['hs_end_date'] = hs_end_date
            if hs_notes is not None:
                body['hs_notes'] = hs_notes
            if hs_audience is not None:
                body['hs_audience'] = hs_audience
            if hs_currency_code is not None:
                body['hs_currency_code'] = hs_currency_code
            if hs_campaign_status is not None:
                body['hs_campaign_status'] = hs_campaign_status
            if hs_utm is not None:
                body['hs_utm'] = hs_utm

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create campaign")

    async def update_campaign(
        self,
        campaign_id: str,
        hs_name: Optional[str] = None,
        hs_start_date: Optional[str] = None,
        hs_end_date: Optional[str] = None,
        hs_notes: Optional[str] = None,
        hs_audience: Optional[str] = None,
        hs_currency_code: Optional[str] = None,
        hs_campaign_status: Optional[Literal["planned", "in_progress", "active", "paused", "completed"]] = None,
        hs_utm: Optional[str] = None
    ) -> HubSpotResponse:
        """Update a campaign

        Args:
            campaign_id: Campaign ID
            hs_name: Campaign name
            hs_start_date: Campaign start date (YYYY-MM-DD)
            hs_end_date: Campaign end date (YYYY-MM-DD)
            hs_notes: Campaign notes
            hs_audience: Campaign audience
            hs_currency_code: Currency code
            hs_campaign_status: Campaign status
            hs_utm: UTM values

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/marketing/v3/campaigns/{campaign_id}".format(campaign_id=campaign_id)

            body = {}
            if hs_name is not None:
                body['hs_name'] = hs_name
            if hs_start_date is not None:
                body['hs_start_date'] = hs_start_date
            if hs_end_date is not None:
                body['hs_end_date'] = hs_end_date
            if hs_notes is not None:
                body['hs_notes'] = hs_notes
            if hs_audience is not None:
                body['hs_audience'] = hs_audience
            if hs_currency_code is not None:
                body['hs_currency_code'] = hs_currency_code
            if hs_campaign_status is not None:
                body['hs_campaign_status'] = hs_campaign_status
            if hs_utm is not None:
                body['hs_utm'] = hs_utm

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="PATCH",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update campaign")

    async def delete_campaign(
        self,
        campaign_id: str
    ) -> HubSpotResponse:
        """Delete a campaign

        Args:
            campaign_id: Campaign ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/marketing/v3/campaigns/{campaign_id}".format(campaign_id=campaign_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete campaign")

    async def list_conversations(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all conversation threads

        Args:
            limit: Number of results to return
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))

            url = self.base_url + "/conversations/v3/conversations/threads"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list conversations")

    async def get_conversation(
        self,
        thread_id: str
    ) -> HubSpotResponse:
        """Get a conversation thread by ID

        Args:
            thread_id: Thread ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/conversations/v3/conversations/threads/{thread_id}".format(thread_id=thread_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get conversation")

    async def list_conversation_messages(
        self,
        thread_id: str,
        limit: Optional[int] = None,
        after: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all messages from a conversation thread

        Args:
            thread_id: Thread ID
            limit: Number of results to return
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))

            url = self.base_url + "/conversations/v3/conversations/threads/{thread_id}/messages".format(thread_id=thread_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list conversation messages")

    async def send_message(
        self,
        thread_id: str,
        type: str,
        text: str,
        channel_account_id: str,
        channel_id: str
    ) -> HubSpotResponse:
        """Send a message to a conversation thread

        Args:
            thread_id: Thread ID
            type: Message type
            text: Message text content
            channel_account_id: Channel account ID
            channel_id: Channel ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/conversations/v3/conversations/threads/{thread_id}/messages".format(thread_id=thread_id)

            body = {}
            body['type'] = type
            body['text'] = text
            body['channel_account_id'] = channel_account_id
            body['channel_id'] = channel_id

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute send message")

    async def list_workflows(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all workflows

        Args:
            limit: Number of results to return
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))

            url = self.base_url + "/automation/v4/workflows"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list workflows")

    async def get_workflow(
        self,
        workflow_id: str
    ) -> HubSpotResponse:
        """Get a workflow by ID

        Args:
            workflow_id: Workflow ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/automation/v4/workflows/{workflow_id}".format(workflow_id=workflow_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get workflow")

    async def create_workflow(
        self,
        name: str,
        type: str,
        enabled: Optional[bool] = None,
        actions: Optional[List[Dict[str, Any]]] = None,
        trigger: Optional[Dict[str, Any]] = None
    ) -> HubSpotResponse:
        """Create a new workflow

        Args:
            name: Workflow name
            type: Workflow type
            enabled: Whether workflow is enabled
            actions: Workflow actions
            trigger: Workflow trigger configuration

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/automation/v4/workflows"

            body = {}
            body['name'] = name
            body['type'] = type
            if enabled is not None:
                body['enabled'] = enabled
            if actions is not None:
                body['actions'] = actions
            if trigger is not None:
                body['trigger'] = trigger

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create workflow")

    async def send_custom_event(
        self,
        event_name: str,
        utk: Optional[str] = None,
        email: Optional[str] = None,
        object_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        occurred_at: Optional[str] = None
    ) -> HubSpotResponse:
        """Send a custom behavioral event

        Args:
            event_name: Event name
            utk: User token
            email: Contact email
            object_id: Associated object ID
            properties: Event properties
            occurred_at: Event timestamp (ISO 8601)

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/events/v3/send"

            body = {}
            body['eventName'] = event_name
            if utk is not None:
                body['utk'] = utk
            if email is not None:
                body['email'] = email
            if object_id is not None:
                body['objectId'] = object_id
            if properties is not None:
                body['properties'] = properties
            if occurred_at is not None:
                body['occurredAt'] = occurred_at

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute send custom event")

    async def list_event_definitions(
        self,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all event definitions

        Args:
            created_after: Filter definitions created after date
            created_before: Filter definitions created before date
            updated_after: Filter definitions updated after date
            updated_before: Filter definitions updated before date

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if created_after is not None:
                query_params.append(('createdAfter', str(created_after)))
            if created_before is not None:
                query_params.append(('createdBefore', str(created_before)))
            if updated_after is not None:
                query_params.append(('updatedAfter', str(updated_after)))
            if updated_before is not None:
                query_params.append(('updatedBefore', str(updated_before)))

            url = self.base_url + "/events/v3/event-definitions"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list event definitions")

    async def create_event_definition(
        self,
        name: str,
        label: str,
        primary_object: str,
        description: Optional[str] = None,
        properties: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new event definition

        Args:
            name: Event definition name
            label: Human-readable label
            description: Event description
            primary_object: Primary object type for the event
            properties: Event property definitions

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/events/v3/event-definitions"

            body = {}
            body['name'] = name
            body['label'] = label
            if description is not None:
                body['description'] = description
            body['primaryObject'] = primary_object
            if properties is not None:
                body['properties'] = properties

                headers = self.http.headers.copy()
                headers["Content-Type"] = "application/json"

                request = HTTPRequest(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=json.dumps(body)
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create event definition")

    async def list_payments(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get all payments

        Args:
            limit: Number of results to return
            after: Pagination cursor
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))

            url = self.base_url + "/crm/v3/objects/payments"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list payments")

    async def get_payment(
        self,
        payment_id: str,
        properties: Optional[List[str]] = None,
        associations: Optional[List[str]] = None
    ) -> HubSpotResponse:
        """Get a payment by ID

        Args:
            payment_id: Payment ID
            properties: List of properties to return
            associations: List of object types to retrieve associated IDs for

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if properties is not None:
                query_params.append(('properties', ','.join(properties)))
            if associations is not None:
                query_params.append(('associations', ','.join(associations)))

            url = self.base_url + "/crm/v3/objects/payments/{payment_id}".format(payment_id=payment_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get payment")

    async def create_payment(
        self,
        properties: Dict[str, Any],
        associations: Optional[List[Dict[str, Any]]] = None
    ) -> HubSpotResponse:
        """Create a new payment

        Args:
            properties: Payment property values
            associations: List of objects to associate with this payment

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/payments"

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create payment")

    async def update_payment(
        self,
        payment_id: str,
        properties: Dict[str, Any]
    ) -> HubSpotResponse:
        """Update a payment

        Args:
            payment_id: Payment ID
            properties: Payment property values to update

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/crm/v3/objects/payments/{payment_id}".format(payment_id=payment_id)

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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update payment")

    async def list_webhook_subscriptions(
        self
    ) -> HubSpotResponse:
        """Get all webhook subscriptions

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/webhooks/v3/subscriptions"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list webhook subscriptions")

    async def get_webhook_subscription(
        self,
        subscription_id: str
    ) -> HubSpotResponse:
        """Get a webhook subscription by ID

        Args:
            subscription_id: Subscription ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/webhooks/v3/subscriptions/{subscription_id}".format(subscription_id=subscription_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get webhook subscription")

    async def create_webhook_subscription(
        self,
        event_type: str,
        property_name: Optional[str] = None,
        active: Optional[bool] = None
    ) -> HubSpotResponse:
        """Create a new webhook subscription

        Args:
            event_type: Event type to subscribe to
            property_name: Property name (for property change events)
            active: Whether subscription is active

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/webhooks/v3/subscriptions"

            body = {}
            body['eventType'] = event_type
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute create webhook subscription")

    async def update_webhook_subscription(
        self,
        subscription_id: str,
        active: Optional[bool] = None
    ) -> HubSpotResponse:
        """Update a webhook subscription

        Args:
            subscription_id: Subscription ID
            active: Whether subscription is active

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/webhooks/v3/subscriptions/{subscription_id}".format(subscription_id=subscription_id)

            body = {}
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

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute update webhook subscription")

    async def delete_webhook_subscription(
        self,
        subscription_id: str
    ) -> HubSpotResponse:
        """Delete a webhook subscription

        Args:
            subscription_id: Subscription ID

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/webhooks/v3/subscriptions/{subscription_id}".format(subscription_id=subscription_id)

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="DELETE",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute delete webhook subscription")

    async def get_account_info(
        self
    ) -> HubSpotResponse:
        """Get account information

        Returns:
            HubSpotResponse with operation result
        """
        try:
            url = self.base_url + "/account-info/v3/details"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get account info")

    async def get_account_activity(
        self,
        occurred_after: Optional[str] = None,
        occurred_before: Optional[str] = None,
        limit: Optional[int] = None
    ) -> HubSpotResponse:
        """Get account activity information

        Args:
            occurred_after: Filter activities after this date
            occurred_before: Filter activities before this date
            limit: Number of results to return

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if occurred_after is not None:
                query_params.append(('occurredAfter', str(occurred_after)))
            if occurred_before is not None:
                query_params.append(('occurredBefore', str(occurred_before)))
            if limit is not None:
                query_params.append(('limit', str(limit)))

            url = self.base_url + "/account-activity/v3/activity"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get account activity")

    async def list_users(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all users

        Args:
            limit: Number of results to return
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))

            url = self.base_url + "/settings/v3/users"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list users")

    async def get_user(
        self,
        user_id: str,
        id_property: Optional[str] = None
    ) -> HubSpotResponse:
        """Get a user by ID

        Args:
            user_id: User ID
            id_property: Property to use as user identifier

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if id_property is not None:
                query_params.append(('idProperty', str(id_property)))

            url = self.base_url + "/settings/v3/users/{user_id}".format(user_id=user_id)
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute get user")

    async def list_teams(
        self,
        limit: Optional[int] = None,
        after: Optional[str] = None
    ) -> HubSpotResponse:
        """Get all teams

        Args:
            limit: Number of results to return
            after: Pagination cursor

        Returns:
            HubSpotResponse with operation result
        """
        try:
            query_params = []
            if limit is not None:
                query_params.append(('limit', str(limit)))
            if after is not None:
                query_params.append(('after', str(after)))

            url = self.base_url + "/settings/v3/teams"
                if query_params:
                    query_string = urlencode(query_params)
                    url += f"?{query_string}"

                headers = self.http.headers.copy()

                request = HTTPRequest(
                    method="GET",
                    url=url,
                    headers=headers
                )

                response = await self.http.execute(request)
                return HubSpotResponse(success=True, data=response)

        except Exception as e:
            return HubSpotResponse(success=False, error=str(e), message=f"Failed to execute list teams")

    def get_client_info(self) -> HubSpotResponse:
        """Get information about the HubSpot client."""
        info = {
            'total_methods': 85,
            'base_url': self.base_url,
            'api_categories': [
                'CRM API (Contacts, Companies, Deals, Tickets, Properties, Associations)',
                'CMS API (Pages, Blog Posts, HubDB, Authors, Tags)',
                'Marketing API (Campaigns, Email, Social)',
                'Conversations API (Messages, Threads, Channels)',
                'Automation API (Workflows, Sequences)',
                'Events API (Custom Events, Behavioral Events)',
                'Commerce API (Payments, Subscriptions)',
                'Webhooks API (Subscriptions, Events)',
                'Account API (Info, Activity, Usage)',
                'Settings API (Users, Teams, Business Units)'
            ]
        }
        return HubSpotResponse(success=True, data=info)