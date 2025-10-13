# Airtable Tools README

## Overview

Airtable tools enable agents to manage databases and spreadsheets including records, bases, and tables with full CRUD operations.

## Architecture

Three-layer pattern:
1. **Client** (`app/sources/client/airtable/airtable.py`) - Authentication & HTTP
2. **DataSource** (`app/sources/external/airtable/airtable.py`) - Async API methods
3. **Tools** (`app/agents/actions/airtable/airtable.py`) - Sync `@tool` methods

## Available Tools

- `create_record` - Create a new record in a table
- `get_record` - Get a specific record by ID
- `list_records` - List records with filtering and pagination
- `update_record` - Update an existing record
- `delete_record` - Delete a record
- `list_bases` - List all accessible bases
- `get_base_schema` - Get base schema (tables/fields)
- `search_records` - Search records by query

All tools return `(bool, str)` with JSON response.

## End-to-End Example: Create Record

### 1. Configure Client

Store credentials in etcd:
```bash
etcdctl put /services/connectors/airtable/config '{
  "auth_type": "token",
  "token": "patXXXXXXXXXXXXXX",
  "base_url": "https://api.airtable.com/v0"
}'
```

### 2. Create Factory

File: `app/agents/tools/factories/airtable.py`
```python
from app.agents.tools.factories.base import ClientFactory
from app.sources.client.airtable.airtable import AirtableClient

class AirtableClientFactory(ClientFactory):
    async def create_client(self, config_service, logger) -> AirtableClient:
        return await AirtableClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
```

### 3. Register Factory

File: `app/agents/tools/factories/registry.py`
```python
from app.agents.tools.factories.airtable import AirtableClientFactory

elif app_name == "airtable":
    cls.register(app_name, AirtableClientFactory())
```

### 4. Configure Discovery

File: `app/agents/tools/config.py`
```python
ToolDiscoveryConfig.APP_CONFIGS.update({
    "airtable": AppConfiguration(
        app_name="airtable",
        client_builder="AirtableClient"
    )
})
```

### 5. Implement DataSource

File: `app/sources/external/airtable/airtable.py`
```python
from typing import Any, Dict, Optional
from app.sources.client.airtable.airtable import AirtableClient, AirtableResponse
from app.sources.client.http.http_request import HTTPRequest

class AirtableDataSource:
    def __init__(self, client: AirtableClient) -> None:
        self._client = client
        self.http = client.get_client()
        self.base_url = self.http.get_base_url()

    async def create_record(
        self,
        base_id: str,
        table_id: str,
        fields: Dict[str, Any],
        typecast: Optional[bool] = False
    ) -> AirtableResponse:
        """Create a new record"""
        url = f"{self.base_url}/{base_id}/{table_id}"
        
        body = {"fields": fields}
        if typecast:
            body["typecast"] = typecast
        
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.http.headers,
            body=body
        )
        
        response = await self.http.execute(request)
        
        return AirtableResponse(
            success=response.status < 400,
            data=response.json() if response.status < 400 else None,
            error=response.text if response.status >= 400 else None
        )
```

### 6. Use the Tool

```python
from app.sources.client.airtable.airtable import AirtableClient, AirtableTokenConfig
from app.agents.actions.airtable.airtable import Airtable

# Build client
config = AirtableTokenConfig(token="patXXXXXXXXXXXXXX")
client = AirtableClient.build_with_config(config)

# Create tool instance
airtable = Airtable(client)

# Create a record
success, result = airtable.create_record(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    fields={
        "Name": "Design API endpoints",
        "Status": "In Progress",
        "Priority": "High",
        "Due Date": "2025-12-31",
        "Assignee": ["recUserID123"]
    },
    typecast=True
)

print(f"Success: {success}")
print(f"Result: {result}")
# Output: {"success": true, "message": "Record created successfully", "data": {...}}
```

## Tool Usage Examples

### Create Record
```python
# Create a task
success, result = airtable.create_record(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    fields={
        "Name": "Implement user authentication",
        "Status": "To Do",
        "Priority": "High",
        "Estimated Hours": 8
    },
    typecast=True
)

# Create with linked records
success, result = airtable.create_record(
    base_id="appAbC123XYZ",
    table_id="Projects",
    fields={
        "Project Name": "Website Redesign",
        "Team Members": ["recUser1", "recUser2"],
        "Budget": 50000
    }
)
```

### Get Record
```python
success, result = airtable.get_record(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    record_id="recXXXXXXXXXXXXXX"
)
# Returns: {"success": true, "data": {"id": "rec...", "fields": {...}}}
```

### List Records
```python
# List all records
success, result = airtable.list_records(
    base_id="appAbC123XYZ",
    table_id="Tasks"
)

# List with filtering
success, result = airtable.list_records(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    filter_by_formula="AND({Status} = 'In Progress', {Priority} = 'High')",
    max_records=50
)

# List with sorting
success, result = airtable.list_records(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    sort_field="Due Date",
    sort_direction="asc"
)
```

### Update Record
```python
success, result = airtable.update_record(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    record_id="recXXXXXXXXXXXXXX",
    fields={
        "Status": "Completed",
        "Completion Date": "2025-10-13"
    }
)
```

### Delete Record
```python
success, result = airtable.delete_record(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    record_id="recXXXXXXXXXXXXXX"
)
```

### List Bases
```python
success, result = airtable.list_bases()
# Returns: {"success": true, "data": {"bases": [...]}}
```

### Get Base Schema
```python
success, result = airtable.get_base_schema(
    base_id="appAbC123XYZ"
)
# Returns: {"success": true, "data": {"tables": [...]}}
```

### Search Records
```python
success, result = airtable.search_records(
    base_id="appAbC123XYZ",
    table_id="Tasks",
    search_query="authentication",
    fields=["Name", "Description"]
)
```

## Airtable Formula Language

Use formulas to filter records:

### Basic Formulas
```javascript
// Equal
"{Status} = 'Done'"

// Not equal
"{Priority} != 'Low'"

// Greater than
"{Estimated Hours} > 5"

// Contains
"FIND('urgent', LOWER({Description})) > 0"
```

### Logical Operators
```javascript
// AND
"AND({Status} = 'In Progress', {Priority} = 'High')"

// OR
"OR({Status} = 'To Do', {Status} = 'In Progress')"

// NOT
"NOT({Status} = 'Completed')"
```

### Date Formulas
```javascript
// Today
"IS_SAME({Due Date}, TODAY(), 'day')"

// This week
"AND(IS_AFTER({Due Date}, TODAY()), IS_BEFORE({Due Date}, DATEADD(TODAY(), 7, 'days')))"

// Past due
"IS_BEFORE({Due Date}, TODAY())"
```

### Text Formulas
```javascript
// Search in text
"SEARCH('keyword', {Name}) > 0"

// Multiple conditions
"OR(FIND('bug', LOWER({Title})), FIND('error', LOWER({Description})))"
```

## Field Types

Common Airtable field types:
- **Single line text** - Short text
- **Long text** - Paragraphs
- **Attachment** - Files and images
- **Checkbox** - Boolean true/false
- **Multiple select** - Array of options
- **Single select** - One option
- **Date** - Date only
- **Phone number** - Formatted phone
- **Email** - Email address
- **URL** - Web link
- **Number** - Numeric values
- **Currency** - Money values
- **Percent** - Percentage values
- **Duration** - Time duration
- **Rating** - Star rating
- **Formula** - Calculated field
- **Rollup** - Aggregate from linked records
- **Count** - Count linked records
- **Lookup** - Pull fields from linked records
- **Created time** - Auto timestamp
- **Last modified time** - Auto timestamp
- **Created by** - Auto user
- **Last modified by** - Auto user

## Linked Records

```python
# Link to other records using record IDs
fields = {
    "Task Name": "Review code",
    "Assigned To": ["recUser123"],  # Link to Users table
    "Project": ["recProject456"]     # Link to Projects table
}
```

## Typecast

The `typecast` parameter automatically converts field values:
```python
# Without typecast - must match exact format
fields = {
    "Date": "2025-10-13",
    "Number": 42
}

# With typecast - more flexible
fields = {
    "Date": "October 13, 2025",  # Converts to proper format
    "Number": "42"                # Converts string to number
}
```

## Rate Limits

Airtable API rate limits:
- **5 requests per second** per base
- Exceeding limit returns 429 error
- Implement retry logic with exponential backoff

## Best Practices

### Record Creation
- Use `typecast=True` for flexible field formatting
- Validate required fields before creating
- Use batch operations for multiple records
- Handle linked records with proper IDs

### Filtering
- Use formulas instead of fetching all records
- Combine filters with AND/OR for efficiency
- Index frequently filtered fields
- Use views for complex filtering

### Performance
- Request only needed fields using `fields` parameter
- Use `max_records` to limit results
- Implement pagination for large datasets
- Cache base schemas to reduce API calls

## Adding New Tools

Pattern for adding new tools:
```python
@tool(
    app_name="airtable",
    tool_name="your_tool_name",
    parameters=[
        ToolParameter(
            name="base_id",
            type=ParameterType.STRING,
            description="Base ID",
            required=True
        )
    ]
)
def your_tool_name(self, base_id: str) -> Tuple[bool, str]:
    try:
        response = self._run_async(
            self.client.your_datasource_method(base_id)
        )
        return self._handle_response(response, "Success message")
    except Exception as e:
        logger.error(f"Error: {e}")
        return False, json.dumps({"success": False, "error": str(e)})
```

## Testing

```python
import asyncio
from app.sources.client.airtable.airtable import AirtableClient, AirtableTokenConfig
from app.agents.actions.airtable.airtable import Airtable

async def test_airtable():
    config = AirtableTokenConfig(token="patXXXXXXXXXXXXXX")
    client = AirtableClient.build_with_config(config)
    airtable = Airtable(client)
    
    # Test record creation
    success, result = airtable.create_record(
        base_id="appTestBase",
        table_id="TestTable",
        fields={"Name": "Test Record"},
        typecast=True
    )
    
    print(f"Success: {success}")
    print(f"Result: {result}")

if __name__ == "__main__":
    asyncio.run(test_airtable())
```
