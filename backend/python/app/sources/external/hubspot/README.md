# HubSpot Integration for Pipeshub AI

This integration provides a complete REST client for interacting with HubSpot's CRM APIs.

## Overview

The HubSpot integration includes:
- **Client Module** (`app/sources/client/hubspot/`): Authentication and SDK wrapper
- **Data Source Module** (`app/sources/external/hubspot/`): Business logic for CRM operations
- **Example Scripts**: Demonstrating usage patterns

## Features

### Supported CRM Objects
- **Contacts**: Create, read, update, delete, and search contacts
- **Companies**: Manage company records
- **Deals**: Track sales opportunities
- **Tickets**: Handle customer support tickets

### Authentication
Uses HubSpot's official Python SDK (`hubspot-api-client`) with token-based authentication:
- Private App Tokens
- OAuth Access Tokens

## Installation

1. Install the required dependency:
```bash
pip install hubspot-api-client>=9.2.0
```

2. The dependency is already added to `pyproject.toml`.

## Quick Start

### 1. Get Your HubSpot Access Token

#### Option A: Create a Private App (Recommended for Development)
1. Go to **Settings > Integrations > Private Apps** in your HubSpot account
2. Click **Create a private app**
3. Configure the following scopes:
   - `crm.objects.contacts.read`
   - `crm.objects.contacts.write`
   - `crm.objects.companies.read`
   - `crm.objects.companies.write`
   - `crm.objects.deals.read`
   - `crm.objects.deals.write`
   - `crm.objects.tickets.read`
   - `crm.objects.tickets.write`
4. Install the app and copy the access token

#### Option B: Use OAuth (For Production)
Follow HubSpot's OAuth documentation to obtain an access token.

### 2. Use the Client

```python
import asyncio
from app.sources.client.hubspot.hubspot_ import HubSpotClient, HubSpotTokenConfig
from app.sources.external.hubspot.hubspot_ import HubSpotDataSource

async def main():
    # Initialize client
    client = HubSpotClient.build_with_config(
        HubSpotTokenConfig(token="your-access-token")
    )
    
    # Create data source
    data_source = HubSpotDataSource(client)
    
    # Get contacts
    contacts = await data_source.get_contacts(limit=10)
    print(f"Found {len(contacts.get('results', []))} contacts")
    
    # Create a contact
    new_contact = await data_source.create_contact(
        properties={
            "email": "john.doe@example.com",
            "firstname": "John",
            "lastname": "Doe",
        }
    )
    print(f"Created contact: {new_contact.get('id')}")

if __name__ == "__main__":
    asyncio.run(main())
```

## API Reference

### HubSpotClient

Located in `app/sources/client/hubspot/hubspot_.py`

#### Methods
- `build_with_config(config: HubSpotTokenConfig) -> HubSpotClient`: Create client from configuration
- `get_client() -> HubSpotSDK`: Get the underlying HubSpot SDK client

### HubSpotDataSource

Located in `app/sources/external/hubspot/hubspot_.py`

#### Contact Methods
- `get_contacts(limit, properties, archived)`: List contacts
- `get_contact_by_id(contact_id, properties, archived)`: Get specific contact
- `create_contact(properties)`: Create new contact
- `update_contact(contact_id, properties)`: Update existing contact
- `delete_contact(contact_id)`: Archive contact
- `search_contacts(filter_groups, properties, limit, after)`: Search contacts

#### Company Methods
- `get_companies(limit, properties, archived)`: List companies
- `get_company_by_id(company_id, properties, archived)`: Get specific company
- `create_company(properties)`: Create new company
- `update_company(company_id, properties)`: Update existing company
- `delete_company(company_id)`: Archive company
- `search_companies(filter_groups, properties, limit, after)`: Search companies

#### Deal Methods
- `get_deals(limit, properties, archived)`: List deals
- `get_deal_by_id(deal_id, properties, archived)`: Get specific deal
- `create_deal(properties)`: Create new deal
- `update_deal(deal_id, properties)`: Update existing deal
- `delete_deal(deal_id)`: Archive deal

#### Ticket Methods
- `get_tickets(limit, properties, archived)`: List tickets
- `get_ticket_by_id(ticket_id, properties, archived)`: Get specific ticket
- `create_ticket(properties)`: Create new ticket
- `update_ticket(ticket_id, properties)`: Update existing ticket
- `delete_ticket(ticket_id)`: Archive ticket

## Examples

### Running the Example Script

```bash
export HUBSPOT_TOKEN="your-access-token"
cd backend/python
PYTHONPATH=. python3 app/sources/external/hubspot/example.py
```

The example script demonstrates:
- Creating, reading, updating, and deleting contacts
- Managing companies and deals
- Working with tickets
- Using the search API

### Testing Your Implementation

```bash
cd backend/python
PYTHONPATH=. python3 app/sources/external/hubspot/test_implementation.py
```

This verifies:
- All modules import correctly
- Client initialization works
- Data source methods are present
- API signatures are correct

## Code Quality

All code has been validated with ruff:

```bash
python3 -m ruff check app/sources/client/hubspot/ --fix
python3 -m ruff check app/sources/external/hubspot/ --fix
```

## Architecture

### File Structure
```
backend/python/
├── app/sources/client/hubspot/
│   ├── __init__.py          # Public exports
│   └── hubspot_.py          # Client implementation
├── app/sources/external/hubspot/
│   ├── __init__.py          # Public exports
│   ├── hubspot_.py          # Data source implementation
│   ├── example.py           # Usage examples
│   └── test_implementation.py  # Verification tests
└── code-generator/
    └── hubspot.py           # Code generator (for reference)
```

### Design Patterns

1. **Naming Convention**: Files are named `hubspot_.py` (with underscore) to avoid conflicts with the `hubspot` package name
2. **Async/Await**: All API methods are async for better performance
3. **Type Hints**: Full type annotations for better IDE support
4. **Error Handling**: Comprehensive error messages with context
5. **SDK Wrapper**: Uses official HubSpot SDK rather than raw HTTP calls

## Troubleshooting

### Common Issues

1. **ModuleNotFoundError: No module named 'hubspot'**
   - Install the SDK: `pip install hubspot-api-client`

2. **Circular Import Errors**
   - Ensure you're importing from `hubspot_` (with underscore), not `hubspot`

3. **Authentication Errors**
   - Verify your token has the required scopes
   - Check token hasn't expired (private app tokens don't expire)

4. **API Rate Limits**
   - HubSpot has rate limits. The SDK handles retries automatically
   - Consider implementing backoff strategies for high-volume operations

## Contributing

When extending this integration:

1. Follow the existing naming conventions (`hubspot_.py`)
2. Add type hints to all methods
3. Write comprehensive docstrings
4. Run ruff to check code quality
5. Test with actual HubSpot API (use a test account)
6. Update this README with new features

## References

- [HubSpot API Documentation](https://developers.hubspot.com/docs/api/overview)
- [hubspot-api-client Python SDK](https://github.com/HubSpot/hubspot-api-python)
- [HubSpot CRM API](https://developers.hubspot.com/docs/api/crm/understanding-the-crm)
- [Private Apps Documentation](https://developers.hubspot.com/docs/api/private-apps)

## License

This integration follows the same license as the main Pipeshub AI project.
