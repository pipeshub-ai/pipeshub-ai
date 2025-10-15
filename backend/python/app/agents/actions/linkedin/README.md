# LinkedIn Connector
## Overview
[`LinkedIn`](https://www.linkedin.com/) is a professional networking platform. Its API supports identity, content creation, and search features for business workflows.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/linkedin.png" alt="LinkedIn Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`LinkedIn Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/linkedin/linkedin.py) - creates LinkedIn client.
<!--([`Local`](/backend/python/app/sources/client/linkedin/linkedin.py))-->

- [`LinkedIn APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/linkedin/linkedin.py) - provides methods to connect to LinkedIn APIs.
<!--([`Local`](/backend/python/app/sources/external/linkedin/linkedin.py))-->

- [`LinkedIn Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/linkedin/linkedin.py) - actions that AI agents can do on LinkedIn (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/linkedin/linkedin.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `get_userinfo` | Get current user information (OIDC) | - |
| `create_post` | Create a post | `author`, `commentary`, `visibility (Optional)`, `lifecycle_state (Optional)` |
| `get_post` | Get a post by ID | `post_id` |
| `update_post` | Update a post | `post_id`, `patch_data` |
| `delete_post` | Delete a post | `post_id` |
| `search_people` | Search for people | `keywords`, `query_params (Optional)` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new LinkedIn action to Agent
-----
#### 1. Go to [`LinkedIn Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/linkedin/linkedin.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to create a post:
```python
def create_post(
    self,
    author: str,
    commentary: str,
    visibility: str = "PUBLIC",
    lifecycle_state: str = "PUBLISHED",
    query_params: Optional[Dict[str, object]] = None
) -> object:
    ...
```

#### 2. Add the tool in this [`LinkedIn Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/linkedin/linkedin.py) like below:
```python
@tool(
    app_name="linkedin",
    tool_name="create_post",
    description="Create a new post on LinkedIn",
    parameters=[
        ToolParameter(
            name="author",
            type=ParameterType.STRING,
            description="Author URN (e.g., 'urn:li:person:AbCdEfG')"
        ),
        ToolParameter(
            name="commentary",
            type=ParameterType.STRING,
            description="Post text content"
        ),
        ToolParameter(
            name="visibility",
            type=ParameterType.STRING,
            description="Post visibility ('PUBLIC', 'CONNECTIONS', etc.)",
            required=False
        ),
        ToolParameter(
            name="lifecycle_state",
            type=ParameterType.STRING,
            description="Post lifecycle state ('PUBLISHED' or 'DRAFT')",
            required=False
        )
    ],
    returns="JSON with post creation result"
)
def create_post(
    self,
    author: str,
    commentary: str,
    visibility: Optional[str] = "PUBLIC",
    lifecycle_state: Optional[str] = "PUBLISHED"
) -> Tuple[bool, str]:
    """Create a new post on LinkedIn."""
    try:
        response = self.client.create_post(
            author=author,
            commentary=commentary,
            visibility=visibility,
            lifecycle_state=lifecycle_state
        )
        return self._handle_response(response, "Post created successfully")
    except Exception as e:
        logger.error(f"Error creating post: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `linkedin`
- `tool_name` - intent of the action, e.g. `create_post`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


