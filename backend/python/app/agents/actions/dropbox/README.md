# Dropbox Connector
## Overview
[`Dropbox`](https://dropbox.com/) is a cloud storage and collaboration service. It allows secure storage, synchronization, sharing, and collaboration on files and folders across devices.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/dropbox.png" alt="Dropbox Logo" width="180"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Dropbox Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/dropbox/dropbox_.py) - creates Dropbox client.
<!--([`Local`](/backend/python/app/sources/client/dropbox/dropbox_.py))-->

- [`Dropbox APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/dropbox/dropbox_.py) - provides methods to connect to Dropbox APIs.
<!--([`Local`](/backend/python/app/sources/external/dropbox/dropbox_.py))-->

- [`Dropbox Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/dropbox/dropbox.py) - actions that AI agents can do on Dropbox (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/dropbox/dropbox.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `get_account_info` | Get current account information | - |
| `list_folder` | List contents of a folder | `path`, `recursive (Optional)`, `include_media_info (Optional)`, `include_deleted (Optional)` |
| `get_metadata` | Get metadata for a file or folder | `path`, `include_media_info (Optional)`, `include_deleted (Optional)` |
| `download_file` | Download a file | `path` |
| `upload_file` | Upload a file | `path`, `content`, `mode (Optional)` |
| `delete_file` | Delete a file or folder | `path` |
| `create_folder` | Create a folder | `path` |
| `search` | Search for files and folders | `query`, `path (Optional)`, `max_results (Optional)` |
| `get_shared_link` | Create or fetch shared link | `path`, `settings (Optional)` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Dropbox action to Agent
-----
#### 1. Go to [`Dropbox Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/dropbox/dropbox_.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to list a folder:
```python
async def files_list_folder(
    self,
    path: str,
    recursive: str = False,
    include_media_info: str = False,
    include_deleted: str = False,
    include_has_explicit_shared_members: str = False,
    include_mounted_folders: str = True,
    limit: Optional[str] = None,
    shared_link: Optional[str] = None,
    include_property_groups: Optional[str] = None,
    include_non_downloadable_files: str = True,
    team_folder_id: Optional[str] = None,
    team_member_id: Optional[str] = None,
) -> DropboxResponse:
    ...
```

#### 2. Add the tool in this [`Dropbox Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/dropbox/dropbox.py) like below:
```python
@tool(
    app_name="dropbox",
    tool_name="list_folder",
    description="List contents of a folder",
    parameters=[
        ToolParameter(
            name="path",
            type=ParameterType.STRING,
            description="Path of the folder to list",
            required=True
        ),
        ToolParameter(
            name="recursive",
            type=ParameterType.BOOLEAN,
            description="Whether to list recursively",
            required=False
        ),
        ToolParameter(
            name="include_media_info",
            type=ParameterType.BOOLEAN,
            description="Whether to include media info",
            required=False
        ),
        ToolParameter(
            name="include_deleted",
            type=ParameterType.BOOLEAN,
            description="Whether to include deleted files",
            required=False
        )
    ]
)
def list_folder(
    self,
    path: str,
    recursive: Optional[bool] = None,
    include_media_info: Optional[bool] = None,
    include_deleted: Optional[bool] = None
) -> Tuple[bool, str]:
    """List contents of a folder
    Args:
        path: Path of the folder to list
        recursive: Whether to list recursively
        include_media_info: Whether to include media info
        include_deleted: Whether to include deleted files
    Returns:
        Tuple[bool, str]: True if successful, False otherwise
    """
    try:
        # Use DropboxDataSource method
        response = self._run_async(self.client.files_list_folder(
            path=path,
            recursive=recursive,
            include_media_info=include_media_info,
            include_deleted=include_deleted
        ))

        if response.success:
            return True, response.to_json()
        else:
            return False, response.to_json()
    except Exception as e:
        logger.error(f"Error listing folder: {e}")
        return False, json.dumps({"error": str(e)})

```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `dropbox`
- `tool_name` - intent of the action, e.g. `list_folder`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type