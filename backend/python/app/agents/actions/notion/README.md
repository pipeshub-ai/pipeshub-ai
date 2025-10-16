# Notion Connector
## Overview
[`Notion`](https://www.notion.so/) is a connected workspace for notes, docs, tasks, and databases with a robust API for automation.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/notion.png" alt="Notion Logo" width="180"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Notion Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/notion/notion.py) - creates Notion client.
<!--([`Local`](/backend/python/app/sources/client/notion/notion.py))-->

- [`Notion APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/notion/notion.py) - provides methods to connect to Notion APIs.
<!--([`Local`](/backend/python/app/sources/external/notion/notion.py))-->

- [`Notion Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/notion/notion.py) - actions that AI agents can do on Notion (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/notion/notion.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_page` | Create a page | `parent_id`, `title`, `content (Optional)`, `parent_is_database (Optional)`, `title_property (Optional)` |
| `get_page` | Get a page | `page_id` |
| `update_page` | Update a page title | `page_id`, `title (Optional)` |
| `delete_page` | Archive a page | `page_id` |
| `search` | Search pages/databases | `query (Optional)`, `sort (Optional)`, `filter (Optional)`, `start_cursor (Optional)`, `page_size (Optional)` |
| `list_users` | List users | `start_cursor (Optional)`, `page_size (Optional)` |
| `retrieve_user` | Retrieve a user | `user_id` |
| `create_database` | Create a database | `parent_id`, `title`, `properties` |
| `query_database` | Query a database | `database_id`, `filter (Optional)`, `sorts (Optional)`, `start_cursor (Optional)`, `page_size (Optional)` |
| `get_database` | Get a database | `database_id` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Notion action to Agent
-----
#### 1. Go to [`Notion Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/notion/notion.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get a page:
```python
async def retrieve_page(self, page_id: str, filter_properties: Optional[List[str]] = None, **kwargs) -> NotionResponse:
    ...
```

#### 2. Add the tool in this [`Notion Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/notion/notion.py) like below:
```python
@tool(
    app_name="notion",
    tool_name="get_page",
    description="Get a page from Notion",
    parameters=[
        ToolParameter(
            name="page_id",
            type=ParameterType.STRING,
            description="The ID of the page to retrieve",
            required=True
        ),
    ],
    returns="JSON with page details"
)
def get_page(self, page_id: str) -> Tuple[bool, str]:
    """Get a page from Notion.

    Args:
        page_id: The ID of the page to retrieve

    Returns:
        Tuple of (success, json_response)
    """
    try:
        response = self._run_async(
            self.client.retrieve_page(page_id=page_id)
        )
        return self._handle_response(response, "Page retrieved successfully")

    except Exception as e:
        logger.error(f"Error getting page: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `notion`
- `tool_name` - intent of the action, e.g. `create_page`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


