# BookStack Connector
## Overview
[`BookStack`](https://www.bookstackapp.com/) is an open-source platform for organizing and storing documentation and knowledge bases. It provides a simple hierarchy of Books, Chapters, and Pages with powerful search and tagging.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/bookstack.png" alt="BookStack Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`BookStack Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/bookstack/bookstack.py) - creates BookStack client.
<!--([`Local`](/backend/python/app/sources/client/bookstack/bookstack.py))-->

- [`BookStack APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/bookstack/bookstack.py) - provides methods to connect to BookStack APIs.
<!--([`Local`](/backend/python/app/sources/external/bookstack/bookstack.py))-->

- [`BookStack Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/bookstack/bookstack.py) - actions that AI agents can do on BookStack (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/bookstack/bookstack.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_page` | Create a new page | `name`, `book_id (Optional)`, `chapter_id (Optional)`, `html (Optional)`, `markdown (Optional)`, `tags (Optional)`, `priority (Optional)` |
| `get_page` | Get a page by ID | `page_id` |
| `update_page` | Update an existing page | `page_id`, `name (Optional)`, `book_id (Optional)`, `chapter_id (Optional)`, `html (Optional)`, `markdown (Optional)`, `tags (Optional)`, `priority (Optional)` |
| `delete_page` | Delete a page | `page_id` |
| `search_all` | Search across all content | `query (Optional)`, `page (Optional)`, `count (Optional)` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new BookStack action to Agent
-----
#### 1. Go to [`BookStack Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/bookstack/bookstack.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get a page by ID:
```python
async def get_page(self, id: int) -> BookStackResponse:
    ...
```

#### 2. Add the tool in this [`BookStack Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/bookstack/bookstack.py) like below:
```python
@tool(
    app_name="bookstack",
    tool_name="get_page",
    description="Get a page by ID from BookStack",
    parameters=[
        ToolParameter(
            name="page_id",
            type=ParameterType.INTEGER,
            description="The ID of the page to retrieve"
        )
    ],
    returns="JSON with page data"
)
    def get_page(self, page_id: int) -> Tuple[bool, str]:
        """Get a page by ID from BookStack."""
        try:
            response = self._run_async(
                self.client.get_page(id=page_id)
            )
            return self._handle_response(response, "Page retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting page: {e}")
            return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `bookstack`
- `tool_name` - intent of the action, e.g. `get_page`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type