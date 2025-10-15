# Airtable Connector
## Overview
[`Airtable`](https://airtable.com/) is a cloud-based platform that bridges the gap between spreadsheets and databases. It lets you organize data in customizable tables while maintaining the flexibility to create different views, automate workflows, and collaborate with your team.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/airtable.png" alt="Airtable Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Airtable Client`](https://github.com/backend/python/app/sources/client/airtable/airtable.py) - creates Airtable client.
<!--([`Local`](/backend/python/app/sources/client/airtable/airtable.py))-->

- [`Airtable APIs`](https://github.com/pipeshub-ai/pipeshub-ai/backend/python/app/sources/external/airtable/airtable.py) - provides methods to connect to Airtable apis.
<!--([`Local`](/backend/python/app/sources/external/airtable/airtable.py))-->

- [`Airtable Actions`](https://github.com/pipeshub-ai/pipeshub-ai/backend/python/app/agents/actions/airtable/airtable.py) - actions that AI agents can do on Airtable (marked with the `@tool` decorator)
<!--([`Local`]/backend/python/app/agents/actions/airtable/airtable.py)-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|-------------|------------|
| `create_records` | Add one or more new records to a table | `base_id`, `table_id_or_name`, `records_json`, `typecast` |
| `get_record` | Grab a specific record by its ID | `base_id`, `table_id_or_name`, `record_id` |
| `list_records` | Get a list of records with optional filters | `base_id`, `table_id_or_name`, `view`, `filter_by_formula`, `page_size` |
| `update_records` | Modify existing records | `base_id`, `table_id_or_name`, `records_json`, `typecast`, `destructive_update` |
| `delete_records` | Remove records permanently | `base_id`, `table_id_or_name`, `record_ids` |
| `search_records` | Find records using Airtable's formula syntax | `base_id`, `table_id_or_name`, `filter_by_formula`, `page_size` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Airtable action to Agent
-----
#### 1. Go to [`Airtable Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/backend/python/app/sources/external/airtable/airtable.py)
Find action(method) you want to provide to PipesHub Agent.
For example, if you want to get a single record:
```python
async def get_record(
    self,
    base_id: str,
    table_id_or_name: str,
    record_id: str,
    return_fields_by_field_id: Optional[bool] = None
) -> AirtableResponse:
```

#### 2. Add the tool in this [`Airtable Tool`](https://github.com/pipeshub-ai/pipeshub-ai/backend/python/app/agents/actions/airtable/airtable.py) like below:
```python
# TOOL DECORATOR
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
        # Bridge the async method to sync using our helper
        resp = self._run_async(
            self.client.get_record(
                base_id=base_id,
                table_id_or_name=table_id_or_name,
                record_id=record_id
            )
        )
        # Process the response in a consistent way
        return self._handle_response(
            getattr(resp, "success", False),
            getattr(resp, "data", None),
            getattr(resp, "error", None),
            "Record fetched successfully"
        )
    except Exception as e:
        # Always log errors and return them in a structured format
        logger.error(f"Error getting record: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. airtable
- `tool_name` - intent of the action, e.g. get_record
- `description` - what it does in details, must be descriptive for agent to read, e.g Retrieve a single record by record ID
- `parameters` - parameters with type and purpose
- `returns` - expected response type