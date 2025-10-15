# Box Connector
## Overview
[`Box`](https://box.com/) is a cloud content management and file sharing service for businesses. It allows secure storage, sharing, and collaboration on files and folders with enterprise‑grade security and governance.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/box.png" alt="Box Logo" width="180"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Box Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/box/box.py) - creates Box client.
<!--([`Local`](/backend/python/app/sources/client/box/box.py))-->

- [`Box APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/box/box.py) - provides methods to connect to Box APIs.
<!--([`Local`](/backend/python/app/sources/external/box/box.py))-->

- [`Box Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/box/box.py) - actions that AI agents can do on Box (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/box/box.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `get_file` | Get file information by ID | `file_id` |
| `update_file` | Update file information | `file_id`, `name?`, `parent_id?` |
| `delete_file` | Delete a file | `file_id` |
| `upload_file` | Upload a file (base64 content) | `file_name`, `parent_folder_id`, `file_content`, `file_description?` |
| `search_content` | Search for content | `query`, `limit?`, `offset?`, `scope?`, `file_extensions?`, `content_types?`, `ancestor_folder_ids?` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Box action to Agent
-----
#### 1. Go to [`Box Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/box/box.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get a file by ID:
```python
async def files_get_file_by_id(
    self, 
    file_id: str
) -> BoxResponse:
    ...
```

#### 2. Add the tool in this [`Box Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/box/box.py) like below:
```python
@tool(
    app_name="box",
    tool_name="get_file",
    description="Get file information by ID from Box",
    parameters=[
        ToolParameter(
            name="file_id",
            type=ParameterType.STRING,
            description="The ID of the file to retrieve"
        )
    ],
    returns="JSON with file data"
)
def get_file(self, file_id: str) -> Tuple[bool, str]:
    """Get file information by ID from Box."""
    try:
        response = self._run_async(
            self.client.files_get_file_by_id(file_id=file_id)
        )
        return self._handle_response(response, "File retrieved successfully")
    except Exception as e:
        logger.error(f"Error getting file: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `box`
- `tool_name` - intent of the action, e.g. `get_file`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type
