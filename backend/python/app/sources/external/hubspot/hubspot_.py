"""HubSpot Data Source - Wrapper for HubSpot API operations

This module provides a data source interface for interacting with HubSpot APIs
using the official hubspot-api-client SDK. It wraps common CRM operations including
contacts, companies, deals, tickets, and more.

Example:
    from app.sources.client.hubspot.hubspot import HubSpotClient, HubSpotTokenConfig
    from app.sources.external.hubspot.hubspot import HubSpotDataSource

    client = HubSpotClient.build_with_config(
        HubSpotTokenConfig(token="your-access-token")
    )
    data_source = HubSpotDataSource(client)

    # Get contacts
    contacts = await data_source.get_contacts(limit=10)
"""

from typing import Any, Dict, List, Optional

from app.sources.client.hubspot.hubspot_ import HubSpotClient


class HubSpotDataSource:
    """HubSpot Data Source for accessing HubSpot CRM APIs

    This class provides methods to interact with various HubSpot APIs including:
    - Contacts
    - Companies
    - Deals
    - Tickets
    - Products
    - Quotes
    - And more CRM objects
    """

    def __init__(self, client: HubSpotClient) -> None:
        """Initialize HubSpot data source

        Args:
            client: HubSpotClient instance

        Raises:
            ValueError: If client is not initialized properly
        """
        self._client = client.get_client()
        if self._client is None:
            raise ValueError("HubSpot client is not initialized")

    def get_data_source(self) -> "HubSpotDataSource":
        """Get the data source instance

        Returns:
            Self reference
        """
        return self

    # ==================== Contact APIs ====================

    async def get_contacts(
        self,
        limit: int = 10,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a list of contacts

        Args:
            limit: Maximum number of contacts to return
            properties: List of properties to return
            archived: Whether to include archived contacts

        Returns:
            Dictionary containing contacts data
        """
        try:
            response = self._client.crm.contacts.basic_api.get_page(
                limit=limit,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get contacts: {e}") from e

    async def get_contact_by_id(
        self,
        contact_id: str,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a contact by ID

        Args:
            contact_id: The contact ID
            properties: List of properties to return
            archived: Whether to include archived contacts

        Returns:
            Dictionary containing contact data
        """
        try:
            response = self._client.crm.contacts.basic_api.get_by_id(
                contact_id=contact_id,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get contact {contact_id}: {e}") from e

    async def create_contact(
        self,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a new contact

        Args:
            properties: Contact properties (e.g., email, firstname, lastname)

        Returns:
            Dictionary containing created contact data
        """
        try:
            from hubspot.crm.contacts import (
                SimplePublicObjectInputForCreate,  # type: ignore
            )

            contact_input = SimplePublicObjectInputForCreate(properties=properties)
            response = self._client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=contact_input
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to create contact: {e}") from e

    async def update_contact(
        self,
        contact_id: str,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a contact

        Args:
            contact_id: The contact ID
            properties: Contact properties to update

        Returns:
            Dictionary containing updated contact data
        """
        try:
            from hubspot.crm.contacts import SimplePublicObjectInput  # type: ignore

            contact_input = SimplePublicObjectInput(properties=properties)
            response = self._client.crm.contacts.basic_api.update(
                contact_id=contact_id,
                simple_public_object_input=contact_input,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to update contact {contact_id}: {e}") from e

    async def delete_contact(self, contact_id: str) -> None:
        """Delete (archive) a contact

        Args:
            contact_id: The contact ID
        """
        try:
            self._client.crm.contacts.basic_api.archive(contact_id=contact_id)
        except Exception as e:
            raise RuntimeError(f"Failed to delete contact {contact_id}: {e}") from e

    # ==================== Company APIs ====================

    async def get_companies(
        self,
        limit: int = 10,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a list of companies

        Args:
            limit: Maximum number of companies to return
            properties: List of properties to return
            archived: Whether to include archived companies

        Returns:
            Dictionary containing companies data
        """
        try:
            response = self._client.crm.companies.basic_api.get_page(
                limit=limit,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get companies: {e}") from e

    async def get_company_by_id(
        self,
        company_id: str,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a company by ID

        Args:
            company_id: The company ID
            properties: List of properties to return
            archived: Whether to include archived companies

        Returns:
            Dictionary containing company data
        """
        try:
            response = self._client.crm.companies.basic_api.get_by_id(
                company_id=company_id,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get company {company_id}: {e}") from e

    async def create_company(
        self,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a new company

        Args:
            properties: Company properties (e.g., name, domain, industry)

        Returns:
            Dictionary containing created company data
        """
        try:
            from hubspot.crm.companies import (
                SimplePublicObjectInputForCreate,  # type: ignore
            )

            company_input = SimplePublicObjectInputForCreate(properties=properties)
            response = self._client.crm.companies.basic_api.create(
                simple_public_object_input_for_create=company_input
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to create company: {e}") from e

    async def update_company(
        self,
        company_id: str,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a company

        Args:
            company_id: The company ID
            properties: Company properties to update

        Returns:
            Dictionary containing updated company data
        """
        try:
            from hubspot.crm.companies import SimplePublicObjectInput  # type: ignore

            company_input = SimplePublicObjectInput(properties=properties)
            response = self._client.crm.companies.basic_api.update(
                company_id=company_id,
                simple_public_object_input=company_input,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to update company {company_id}: {e}") from e

    async def delete_company(self, company_id: str) -> None:
        """Delete (archive) a company

        Args:
            company_id: The company ID
        """
        try:
            self._client.crm.companies.basic_api.archive(company_id=company_id)
        except Exception as e:
            raise RuntimeError(f"Failed to delete company {company_id}: {e}") from e

    # ==================== Deal APIs ====================

    async def get_deals(
        self,
        limit: int = 10,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a list of deals

        Args:
            limit: Maximum number of deals to return
            properties: List of properties to return
            archived: Whether to include archived deals

        Returns:
            Dictionary containing deals data
        """
        try:
            response = self._client.crm.deals.basic_api.get_page(
                limit=limit,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get deals: {e}") from e

    async def get_deal_by_id(
        self,
        deal_id: str,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a deal by ID

        Args:
            deal_id: The deal ID
            properties: List of properties to return
            archived: Whether to include archived deals

        Returns:
            Dictionary containing deal data
        """
        try:
            response = self._client.crm.deals.basic_api.get_by_id(
                deal_id=deal_id,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get deal {deal_id}: {e}") from e

    async def create_deal(
        self,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a new deal

        Args:
            properties: Deal properties (e.g., dealname, dealstage, amount)

        Returns:
            Dictionary containing created deal data
        """
        try:
            from hubspot.crm.deals import (
                SimplePublicObjectInputForCreate,  # type: ignore
            )

            deal_input = SimplePublicObjectInputForCreate(properties=properties)
            response = self._client.crm.deals.basic_api.create(
                simple_public_object_input_for_create=deal_input
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to create deal: {e}") from e

    async def update_deal(
        self,
        deal_id: str,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a deal

        Args:
            deal_id: The deal ID
            properties: Deal properties to update

        Returns:
            Dictionary containing updated deal data
        """
        try:
            from hubspot.crm.deals import SimplePublicObjectInput  # type: ignore

            deal_input = SimplePublicObjectInput(properties=properties)
            response = self._client.crm.deals.basic_api.update(
                deal_id=deal_id,
                simple_public_object_input=deal_input,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to update deal {deal_id}: {e}") from e

    async def delete_deal(self, deal_id: str) -> None:
        """Delete (archive) a deal

        Args:
            deal_id: The deal ID
        """
        try:
            self._client.crm.deals.basic_api.archive(deal_id=deal_id)
        except Exception as e:
            raise RuntimeError(f"Failed to delete deal {deal_id}: {e}") from e

    # ==================== Ticket APIs ====================

    async def get_tickets(
        self,
        limit: int = 10,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a list of tickets

        Args:
            limit: Maximum number of tickets to return
            properties: List of properties to return
            archived: Whether to include archived tickets

        Returns:
            Dictionary containing tickets data
        """
        try:
            response = self._client.crm.tickets.basic_api.get_page(
                limit=limit,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get tickets: {e}") from e

    async def get_ticket_by_id(
        self,
        ticket_id: str,
        properties: Optional[List[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """Get a ticket by ID

        Args:
            ticket_id: The ticket ID
            properties: List of properties to return
            archived: Whether to include archived tickets

        Returns:
            Dictionary containing ticket data
        """
        try:
            response = self._client.crm.tickets.basic_api.get_by_id(
                ticket_id=ticket_id,
                properties=properties,
                archived=archived,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to get ticket {ticket_id}: {e}") from e

    async def create_ticket(
        self,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a new ticket

        Args:
            properties: Ticket properties (e.g., subject, content, hs_pipeline_stage)

        Returns:
            Dictionary containing created ticket data
        """
        try:
            from hubspot.crm.tickets import (
                SimplePublicObjectInputForCreate,  # type: ignore
            )

            ticket_input = SimplePublicObjectInputForCreate(properties=properties)
            response = self._client.crm.tickets.basic_api.create(
                simple_public_object_input_for_create=ticket_input
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to create ticket: {e}") from e

    async def update_ticket(
        self,
        ticket_id: str,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Update a ticket

        Args:
            ticket_id: The ticket ID
            properties: Ticket properties to update

        Returns:
            Dictionary containing updated ticket data
        """
        try:
            from hubspot.crm.tickets import SimplePublicObjectInput  # type: ignore

            ticket_input = SimplePublicObjectInput(properties=properties)
            response = self._client.crm.tickets.basic_api.update(
                ticket_id=ticket_id,
                simple_public_object_input=ticket_input,
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to update ticket {ticket_id}: {e}") from e

    async def delete_ticket(self, ticket_id: str) -> None:
        """Delete (archive) a ticket

        Args:
            ticket_id: The ticket ID
        """
        try:
            self._client.crm.tickets.basic_api.archive(ticket_id=ticket_id)
        except Exception as e:
            raise RuntimeError(f"Failed to delete ticket {ticket_id}: {e}") from e

    # ==================== Search API ====================

    async def search_contacts(
        self,
        filter_groups: List[Dict[str, Any]],
        properties: Optional[List[str]] = None,
        limit: int = 10,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search contacts using filters

        Args:
            filter_groups: List of filter groups (each with filters)
            properties: List of properties to return
            limit: Maximum number of results
            after: Pagination cursor

        Returns:
            Dictionary containing search results
        """
        try:
            from hubspot.crm.contacts import PublicObjectSearchRequest  # type: ignore

            search_request = PublicObjectSearchRequest(
                filter_groups=filter_groups,
                properties=properties,
                limit=limit,
                after=after,
            )
            response = self._client.crm.contacts.search_api.do_search(
                public_object_search_request=search_request
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to search contacts: {e}") from e

    async def search_companies(
        self,
        filter_groups: List[Dict[str, Any]],
        properties: Optional[List[str]] = None,
        limit: int = 10,
        after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search companies using filters

        Args:
            filter_groups: List of filter groups (each with filters)
            properties: List of properties to return
            limit: Maximum number of results
            after: Pagination cursor

        Returns:
            Dictionary containing search results
        """
        try:
            from hubspot.crm.companies import PublicObjectSearchRequest  # type: ignore

            search_request = PublicObjectSearchRequest(
                filter_groups=filter_groups,
                properties=properties,
                limit=limit,
                after=after,
            )
            response = self._client.crm.companies.search_api.do_search(
                public_object_search_request=search_request
            )
            return response.to_dict()
        except Exception as e:
            raise RuntimeError(f"Failed to search companies: {e}") from e


# Helper functions for formatting
def _as_str_dict(d: Dict[str, Any]) -> Dict[str, str]:
    """Convert dictionary values to strings"""
    return {k: str(v) for k, v in d.items()}


def _safe_format_url(url: str, params: Dict[str, Any]) -> str:
    """Safely format URL with path parameters"""
    try:
        return url.format(**params)
    except KeyError as e:
        raise ValueError(f"Missing required path parameter: {e}") from e
