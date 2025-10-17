# Azure Blob Connector
## Overview
[`Azure Blob Storage`](https://learn.microsoft.com/azure/storage/blobs/storage-blobs-introduction) is a massively scalable object storage service for unstructured data. It is optimized for storing large amounts of data and supports features like lifecycle management, access tiers, and fine‑grained security controls.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/azure-blob.png" alt="Azure Blob Storage Logo" width="220"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Azure Blob Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/azure/azure_blob.py) - creates Azure Blob client and validates configuration.
<!--([`Local`](/backend/python/app/sources/client/azure/azure_blob.py))-->

- [`Azure Blob APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/azure/azure_blob.py) - provides async wrappers over Azure SDK operations.
<!--([`Local`](/backend/python/app/sources/external/azure/azure_blob.py))-->

- [`Azure Blob Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/azure/azure_blob.py) - actions that AI agents can do on Azure Blob (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/azure/azure_blob.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_container` | Create a new container | `container_name` |
| `get_container` | Get container properties | `container_name` |
| `delete_container` | Delete a container | `container_name` |
| `upload_blob` | Create or overwrite a block blob with text content | `container_name`, `blob_name`, `content` |
| `get_blob` | Get blob properties | `container_name`, `blob_name` |
| `delete_blob` | Delete a blob | `container_name`, `blob_name` |
| `search_blobs_by_tags` | Search blobs across account by tags WHERE clause | `where`, `maxresults` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Azure Blob action to Agent
-----
#### 1. Go to [`Azure Blob Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/azure/azure_blob.py)
Find operation (method) you want to expose to the PipesHub Agent.
For example, if you want to get container properties:
```python
async def get_container_properties(
    self,
    container_name: str,
    timeout: Optional[int] = None,
    lease_id: Optional[str] = None,
    client_request_id: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    **kwargs
) -> AzureBlobResponse:
    ...
```

#### 2. Add the tool in [`Azure Blob Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/azure/azure_blob.py) like below:
```python
# TOOL DECORATOR
@tool(
    app_name="azure_blob",
    tool_name="get_container",
    description="Get container properties",
    parameters=[
        ToolParameter(
            name="container_name",
            type=ParameterType.STRING,
            description="Container name"
        )
    ],
    returns="JSON with container properties"
)
def get_container(self, container_name: str) -> Tuple[bool, str]:
    try:
        resp = self._run_async(
            self.client.get_container_properties(container_name=container_name)
        )
        return self._wrap(
            getattr(resp, "success", False),
            getattr(resp, "data", None),
            getattr(resp, "error", None),
            "Container fetched successfully"
        )
    except Exception as e:
        logger.error(f"get_container error: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `azure_blob`
- `tool_name` - intent of the action, e.g. `get_container`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type
