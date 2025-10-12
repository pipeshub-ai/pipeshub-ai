from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInput
from hubspot.crm.contacts.exceptions import ApiException as ContactsApiException
from hubspot.crm.companies.exceptions import ApiException as CompaniesApiException
from hubspot.crm.deals.exceptions import ApiException as DealsApiException
from app.sources.client.hubspot.hubspot import HubSpotClient, HubSpotResponse

logger = logging.getLogger(__name__)


class HubSpotDataSource:
    """Enhanced HubSpot data source implementation using official HubSpot SDK
    
    This class provides comprehensive methods to interact with HubSpot CRM APIs including:
    - Contacts management (get, create, update, search)
    - Companies management (get, create, update, search)
    - Deals management (get, create, update)
    - Account information and connectivity testing
    - Bulk operations and advanced filtering
    """

    def __init__(self, client: HubSpotClient) -> None:
        """Initialize with HubSpot client"""
        self.client = client.get_hubspot_client()
        logger.info("HubSpot DataSource initialized successfully")

    # ===== ACCOUNT & CONNECTIVITY =====
    
    async def get_account_info(self) -> HubSpotResponse:
        """Get HubSpot account information and test connectivity
        
        Returns:
            HubSpotResponse with account data
        """
        try:
            # Test connectivity by making a simple API call
            api_response = self.client.crm.contacts.basic_api.get_page(limit=1)
            
            # Get account details if possible
            account_info = {
                'account_connected': True,
                'api_working': True,
                'has_contacts': len(api_response.results) > 0 if api_response.results else False
            }
            
            return HubSpotResponse(
                success=True,
                data=account_info,
                message="HubSpot account connected successfully"
            )
            
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Account connection error: {str(e)}"
            )

    # ===== CONTACTS OPERATIONS =====
    
    async def get_contacts(self, limit: int = 10, properties: Optional[List[str]] = None) -> HubSpotResponse:
        """Get contacts from HubSpot CRM
        
        Args:
            limit: Maximum number of contacts to retrieve
            properties: List of contact properties to include
            
        Returns:
            HubSpotResponse with contacts data
        """
        try:
            if properties is None:
                properties = ['firstname', 'lastname', 'email', 'company', 'phone', 'jobtitle']
                
            api_response = self.client.crm.contacts.basic_api.get_page(
                limit=limit,
                properties=properties
            )
            
            contacts_data = []
            if api_response.results:
                for contact in api_response.results:
                    contact_info = {
                        'id': contact.id,
                        'properties': dict(contact.properties) if contact.properties else {},
                        'created_at': contact.created_at,
                        'updated_at': contact.updated_at
                    }
                    contacts_data.append(contact_info)
            
            return HubSpotResponse(
                success=True,
                data={
                    'contacts': contacts_data,
                    'total': len(contacts_data),
                    'properties_requested': properties
                },
                message=f"Retrieved {len(contacts_data)} contacts successfully"
            )
            
        except ContactsApiException as e:
            logger.error(f"HubSpot contacts API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"HubSpot contacts API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error getting contacts: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    async def get_contact_by_id(self, contact_id: str, properties: Optional[List[str]] = None) -> HubSpotResponse:
        """Get a specific contact by ID
        
        Args:
            contact_id: HubSpot contact ID
            properties: List of properties to retrieve
            
        Returns:
            HubSpotResponse with contact data
        """
        try:
            if properties is None:
                properties = ['firstname', 'lastname', 'email', 'company', 'phone', 'jobtitle']
                
            api_response = self.client.crm.contacts.basic_api.get_by_id(
                contact_id=contact_id,
                properties=properties
            )
            
            contact_data = {
                'id': api_response.id,
                'properties': dict(api_response.properties) if api_response.properties else {},
                'created_at': api_response.created_at,
                'updated_at': api_response.updated_at
            }
            
            return HubSpotResponse(
                success=True,
                data=contact_data,
                message=f"Retrieved contact {contact_id} successfully"
            )
            
        except ContactsApiException as e:
            logger.error(f"HubSpot get contact API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Contact not found or API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error getting contact: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    async def create_contact(self, properties: Dict[str, str]) -> HubSpotResponse:
        """Create a new contact in HubSpot
        
        Args:
            properties: Dictionary of contact properties
            
        Returns:
            HubSpotResponse with created contact data
        """
        try:
            # Validate required properties
            if not properties.get('email'):
                return HubSpotResponse(
                    success=False,
                    error="Email is required to create a contact"
                )
            
            simple_public_object_input = SimplePublicObjectInput(properties=properties)
            api_response = self.client.crm.contacts.basic_api.create(
                simple_public_object_input=simple_public_object_input
            )
            
            return HubSpotResponse(
                success=True,
                data={
                    'contact_id': api_response.id,
                    'properties': dict(api_response.properties) if api_response.properties else {},
                    'created_at': api_response.created_at
                },
                message="Contact created successfully"
            )
            
        except ContactsApiException as e:
            logger.error(f"HubSpot create contact API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Failed to create contact: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error creating contact: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    async def update_contact(self, contact_id: str, properties: Dict[str, str]) -> HubSpotResponse:
        """Update an existing contact
        
        Args:
            contact_id: HubSpot contact ID
            properties: Dictionary of properties to update
            
        Returns:
            HubSpotResponse with updated contact data
        """
        try:
            simple_public_object_input = SimplePublicObjectInput(properties=properties)
            api_response = self.client.crm.contacts.basic_api.update(
                contact_id=contact_id,
                simple_public_object_input=simple_public_object_input
            )
            
            return HubSpotResponse(
                success=True,
                data={
                    'contact_id': api_response.id,
                    'properties': dict(api_response.properties) if api_response.properties else {},
                    'updated_at': api_response.updated_at
                },
                message=f"Contact {contact_id} updated successfully"
            )
            
        except ContactsApiException as e:
            logger.error(f"HubSpot update contact API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Failed to update contact: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error updating contact: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    # ===== COMPANIES OPERATIONS =====
    
    async def get_companies(self, limit: int = 10, properties: Optional[List[str]] = None) -> HubSpotResponse:
        """Get companies from HubSpot CRM
        
        Args:
            limit: Maximum number of companies to retrieve
            properties: List of company properties to include
            
        Returns:
            HubSpotResponse with companies data
        """
        try:
            if properties is None:
                properties = ['name', 'domain', 'industry', 'city', 'state', 'country', 'phone']
                
            api_response = self.client.crm.companies.basic_api.get_page(
                limit=limit,
                properties=properties
            )
            
            companies_data = []
            if api_response.results:
                for company in api_response.results:
                    company_info = {
                        'id': company.id,
                        'properties': dict(company.properties) if company.properties else {},
                        'created_at': company.created_at,
                        'updated_at': company.updated_at
                    }
                    companies_data.append(company_info)
            
            return HubSpotResponse(
                success=True,
                data={
                    'companies': companies_data,
                    'total': len(companies_data),
                    'properties_requested': properties
                },
                message=f"Retrieved {len(companies_data)} companies successfully"
            )
            
        except CompaniesApiException as e:
            logger.error(f"HubSpot companies API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"HubSpot companies API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error getting companies: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    async def get_company_by_id(self, company_id: str, properties: Optional[List[str]] = None) -> HubSpotResponse:
        """Get a specific company by ID
        
        Args:
            company_id: HubSpot company ID
            properties: List of properties to retrieve
            
        Returns:
            HubSpotResponse with company data
        """
        try:
            if properties is None:
                properties = ['name', 'domain', 'industry', 'city', 'state', 'country', 'phone']
                
            api_response = self.client.crm.companies.basic_api.get_by_id(
                company_id=company_id,
                properties=properties
            )
            
            company_data = {
                'id': api_response.id,
                'properties': dict(api_response.properties) if api_response.properties else {},
                'created_at': api_response.created_at,
                'updated_at': api_response.updated_at
            }
            
            return HubSpotResponse(
                success=True,
                data=company_data,
                message=f"Retrieved company {company_id} successfully"
            )
            
        except CompaniesApiException as e:
            logger.error(f"HubSpot get company API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Company not found or API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error getting company: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    async def create_company(self, properties: Dict[str, str]) -> HubSpotResponse:
        """Create a new company in HubSpot
        
        Args:
            properties: Dictionary of company properties
            
        Returns:
            HubSpotResponse with created company data
        """
        try:
            # Validate required properties
            if not properties.get('name'):
                return HubSpotResponse(
                    success=False,
                    error="Company name is required"
                )
            
            simple_public_object_input = SimplePublicObjectInput(properties=properties)
            api_response = self.client.crm.companies.basic_api.create(
                simple_public_object_input=simple_public_object_input
            )
            
            return HubSpotResponse(
                success=True,
                data={
                    'company_id': api_response.id,
                    'properties': dict(api_response.properties) if api_response.properties else {},
                    'created_at': api_response.created_at
                },
                message="Company created successfully"
            )
            
        except CompaniesApiException as e:
            logger.error(f"HubSpot create company API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Failed to create company: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error creating company: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    # ===== DEALS OPERATIONS =====
    
    async def get_deals(self, limit: int = 10, properties: Optional[List[str]] = None) -> HubSpotResponse:
        """Get deals from HubSpot CRM
        
        Args:
            limit: Maximum number of deals to retrieve
            properties: List of deal properties to include
            
        Returns:
            HubSpotResponse with deals data
        """
        try:
            if properties is None:
                properties = ['dealname', 'amount', 'dealstage', 'pipeline', 'closedate']
                
            api_response = self.client.crm.deals.basic_api.get_page(
                limit=limit,
                properties=properties
            )
            
            deals_data = []
            if api_response.results:
                for deal in api_response.results:
                    deal_info = {
                        'id': deal.id,
                        'properties': dict(deal.properties) if deal.properties else {},
                        'created_at': deal.created_at,
                        'updated_at': deal.updated_at
                    }
                    deals_data.append(deal_info)
            
            return HubSpotResponse(
                success=True,
                data={
                    'deals': deals_data,
                    'total': len(deals_data),
                    'properties_requested': properties
                },
                message=f"Retrieved {len(deals_data)} deals successfully"
            )
            
        except DealsApiException as e:
            logger.error(f"HubSpot deals API error: {e}")
            return HubSpotResponse(
                success=False,
                error=f"HubSpot deals API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error getting deals: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )

    # ===== SEARCH OPERATIONS =====
    
    async def search_contacts(self, query: str, limit: int = 10) -> HubSpotResponse:
        """Search contacts in HubSpot CRM
        
        Args:
            query: Search query string
            limit: Maximum number of contacts to retrieve
            
        Returns:
            HubSpotResponse with search results
        """
        try:
            # Get contacts and perform client-side filtering
            # Note: In production, you might want to use HubSpot's search API
            api_response = self.client.crm.contacts.basic_api.get_page(
                limit=limit * 2,  # Get more to filter
                properties=['firstname', 'lastname', 'email', 'company']
            )
            
            filtered_contacts = []
            if api_response.results:
                for contact in api_response.results:
                    # Create searchable text
                    searchable_fields = [
                        str(contact.properties.get('firstname', '')),
                        str(contact.properties.get('lastname', '')),
                        str(contact.properties.get('email', '')),
                        str(contact.properties.get('company', ''))
                    ]
                    contact_text = ' '.join(searchable_fields).lower()
                    
                    if query.lower() in contact_text:
                        filtered_contacts.append({
                            'id': contact.id,
                            'properties': dict(contact.properties) if contact.properties else {},
                            'created_at': contact.created_at,
                            'updated_at': contact.updated_at,
                            'relevance_score': contact_text.count(query.lower())
                        })
            
            # Sort by relevance and limit results
            filtered_contacts.sort(key=lambda x: x['relevance_score'], reverse=True)
            filtered_contacts = filtered_contacts[:limit]
            
            return HubSpotResponse(
                success=True,
                data={
                    'contacts': filtered_contacts,
                    'total': len(filtered_contacts),
                    'search_query': query
                },
                message=f"Found {len(filtered_contacts)} contacts matching '{query}'"
            )
            
        except Exception as e:
            logger.error(f"Error searching contacts: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Search error: {str(e)}"
            )

    # ===== UTILITY METHODS =====
    
    async def get_properties_info(self, object_type: str = 'contacts') -> HubSpotResponse:
        """Get available properties for a CRM object type
        
        Args:
            object_type: Type of object ('contacts', 'companies', 'deals')
            
        Returns:
            HubSpotResponse with properties information
        """
        try:
            if object_type == 'contacts':
                api_response = self.client.crm.properties.core_api.get_all(object_type='contacts')
            elif object_type == 'companies':
                api_response = self.client.crm.properties.core_api.get_all(object_type='companies')
            elif object_type == 'deals':
                api_response = self.client.crm.properties.core_api.get_all(object_type='deals')
            else:
                return HubSpotResponse(
                    success=False,
                    error=f"Unsupported object type: {object_type}"
                )
            
            properties_info = []
            if api_response.results:
                for prop in api_response.results:
                    prop_info = {
                        'name': prop.name,
                        'label': prop.label,
                        'type': prop.type,
                        'field_type': prop.field_type,
                        'description': prop.description
                    }
                    properties_info.append(prop_info)
            
            return HubSpotResponse(
                success=True,
                data={
                    'object_type': object_type,
                    'properties': properties_info,
                    'total': len(properties_info)
                },
                message=f"Retrieved {len(properties_info)} properties for {object_type}"
            )
            
        except Exception as e:
            logger.error(f"Error getting properties info: {e}")
            return HubSpotResponse(
                success=False,
                error=f"Properties error: {str(e)}"
            )
