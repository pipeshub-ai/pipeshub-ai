# 🚀 Airtable Tools

## 📋 Overview

Airtable tools enable AI agents to interact with Airtable databases through a clean, standardized interface. Agents can perform full CRUD operations on records, bases, and tables with proper error handling and async support.

**🔗 Airtable Developer Documentation:** https://airtable.com/developers

## 🏗️ Architecture

The integration follows a clean three-layer architecture for separation of concerns:

### Layer 1: Client (`app/sources/client/airtable/`)
**Purpose:** Handles authentication and HTTP communication
- **Authentication:** Supports both Personal Access Tokens and OAuth 2.0
- **HTTP Client:** Manages connection pooling, retries, and base configuration
- **Response Models:** Standardized `AirtableResponse` objects

### Layer 2: DataSource (`app/sources/external/airtable/`)
**Purpose:** Provides async API methods for all Airtable endpoints
- **Complete Coverage:** All Airtable APIs (Web, Metadata, Enterprise, OAuth, etc.)
- **Type Safety:** Full type hints for all parameters and responses
- **Error Handling:** Consistent error responses across all methods

### Layer 3: Tools (`app/agents/actions/airtable/`)
**Purpose:** Exposes sync tools for AI agents with the `@tool` decorator
- **Agent Interface:** Simple `(bool, str)` return format for agents
- **Async Bridge:** `_run_async()` method handles sync/async conversion
- **Standardization:** Consistent error handling and response formatting

## 🛠️ Available Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `create_records` | Create one or more records | `base_id`, `table_id_or_name`, `records_json`, `typecast?` |
| `get_record` | Retrieve a single record | `base_id`, `table_id_or_name`, `record_id` |
| `list_records` | List records with filtering | `base_id`, `table_id_or_name`, `view?`, `filter_by_formula?`, `page_size?` |
| `update_records` | Update existing records | `base_id`, `table_id_or_name`, `records_json`, `typecast?`, `destructive_update?` |
| `delete_records` | Delete records by ID | `base_id`, `table_id_or_name`, `record_ids` |
| `search_records` | Search using Airtable formulas | `base_id`, `table_id_or_name`, `filter_by_formula`, `page_size?` |

**Return Format:** All tools return `(success: bool, result: str)` where result is JSON.

## 💡 Example Usage

```python
from app.sources.client.airtable.airtable import AirtableClient, AirtableTokenConfig
from app.agents.actions.airtable.airtable import Airtable

# 🔐 Initialize client with token
config = AirtableTokenConfig(token="patXXXXXXXXXXXXXX")
client = AirtableClient.build_with_config(config)

# 🛠️ Create tools instance
airtable = Airtable(client)

# 📥 Get a specific record
success, result = airtable.get_record(
    base_id="appAbC123XYZ",
    table_id_or_name="Tasks",
    record_id="rec1234567890"
)

if success:
    print("✅ Record fetched successfully!")
    data = json.loads(result)
    print(f"📄 Record: {data['data']}")
else:
    print("❌ Failed to fetch record")
    error = json.loads(result)
    print(f"🔍 Error: {error['error']}")
```

## 🆕 Adding New Tools

### Step-by-Step Guide

#### 1. **Identify the DataSource Method**
First, find the corresponding async method in `app/sources/external/airtable/airtable.py`:
```python
# Example: Adding a tool to get a single record
async def get_record(
    self,
    base_id: str,
    table_id_or_name: str,
    record_id: str,
    return_fields_by_field_id: Optional[bool] = None
) -> AirtableResponse:
```

#### 2. **Create the Tool Method**
Add the tool method to `app/agents/actions/airtable/airtable.py`:

```python
@tool(
    app_name="airtable",
    tool_name="get_record",
    description="Retrieve a single record by record ID",
    parameters=[
        ToolParameter(
            name="base_id",
            type=ParameterType.STRING,
            description="Base ID (starts with 'app')"
        ),
        ToolParameter(
            name="table_id_or_name",
            type=ParameterType.STRING,
            description="Table ID (starts with 'tbl') or table name"
        ),
        ToolParameter(
            name="record_id",
            type=ParameterType.STRING,
            description="Record ID (starts with 'rec')"
        )
    ],
    returns="JSON with record data"
)
def get_record(
    self,
    base_id: str,
    table_id_or_name: str,
    record_id: str
) -> Tuple[bool, str]:
    try:
        # 🔄 Call async DataSource method via sync bridge
        resp = self._run_async(
            self.client.get_record(
                base_id=base_id,
                table_id_or_name=table_id_or_name,
                record_id=record_id
            )
        )

        # 📊 Handle response with consistent format
        return self._handle_response(
            getattr(resp, "success", False),
            getattr(resp, "data", None),
            getattr(resp, "error", None),
            "Record fetched successfully"
        )
    except Exception as e:
        # 🚨 Log and return error response
        logger.error(f"Error getting record: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. **Key Components Explained**

**Decorator Parameters:**
- `app_name`: Must be "airtable" for all Airtable tools
- `tool_name`: Unique identifier for the tool
- `description`: Human-readable description for agents
- `parameters`: Array of `ToolParameter` objects
- `returns`: Description of return value

**Tool Method Structure:**
- **Parameters:** Match DataSource method signature
- **Async Call:** Always use `self._run_async()` to call DataSource methods
- **Response Handling:** Use `self._handle_response()` for consistent format
- **Error Handling:** Log errors and return JSON error responses
- **Return Type:** Always `Tuple[bool, str]` for agent compatibility

#### 4. **Parameter Types**
Use these `ParameterType` values:
- `ParameterType.STRING` - Text fields
- `ParameterType.NUMBER` - Numeric fields
- `ParameterType.BOOLEAN` - True/false fields
- `ParameterType.OBJECT` - JSON objects
- `ParameterType.ARRAY` - Arrays/lists

#### 5. **Testing Your Tool**
```python
# Test the new tool
success, result = airtable.get_record(
    base_id="appAbC123XYZ",
    table_id_or_name="Tasks",
    record_id="rec1234567890"
)
if success:
    print("✅ Tool working correctly!")
else:
    print(f"❌ Tool error: {result}")
```

## 🔧 Tool Development Best Practices

### ✅ Do's
- **Consistent Naming:** Follow existing naming patterns (`snake_case`)
- **Complete Documentation:** Include detailed parameter descriptions
- **Error Handling:** Always catch and log exceptions
- **Type Hints:** Use proper type annotations
- **Return Format:** Always return `(bool, str)` tuple

### ❌ Don'ts
- **No Async Methods:** Tools must be synchronous for agent compatibility
- **No Direct API Calls:** Always use DataSource methods
- **No Custom Response Formats:** Stick to the standard JSON format
- **No Missing Error Handling:** Always handle exceptions gracefully

## 🚨 Troubleshooting

**Common Issues:**
1. **Import Errors:** Ensure all imports are available in the environment
2. **Authentication:** Verify token has correct permissions
3. **Async Issues:** Make sure `_run_async()` is properly implemented
4. **Type Errors:** Check parameter types match DataSource method

**Debug Tips:**
- Check logs for detailed error messages
- Verify Airtable API credentials and permissions
- Test DataSource methods directly before creating tools
- Use the existing tools as reference implementations
