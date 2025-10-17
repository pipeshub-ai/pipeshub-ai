# Linear Connector
## Overview
[`Linear`](https://linear.app/) is an issue tracking and project management tool focused on speed and developer experience, with a GraphQL API.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/linear.png" alt="Linear Logo" width="180"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Linear Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/linear/linear.py) - creates Linear client.
<!--([`Local`](/backend/python/app/sources/client/linear/linear.py))-->

- [`Linear APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/linear/linear.py) - provides methods to connect to Linear GraphQL APIs.
<!--([`Local`](/backend/python/app/sources/external/linear/linear.py))-->

- [`Linear Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/linear/linear.py) - actions that AI agents can do on Linear (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/linear/linear.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `get_viewer` | Get current user info | - |
| `get_user` | Get user by ID | `user_id` |
| `get_teams` | List teams | `first (Optional)`, `after (Optional)` |
| `get_team` | Get team by ID | `team_id` |
| `get_issues` | List issues | `first (Optional)`, `after (Optional)`, `filter (Optional)` |
| `get_issue` | Get issue by ID | `issue_id` |
| `create_issue` | Create issue | `team_id`, `title`, `description (Optional)`, `state_id (Optional)`, `assignee_id (Optional)` |
| `update_issue` | Update issue | `issue_id`, `title (Optional)`, `description (Optional)`, `state_id (Optional)`, `assignee_id (Optional)` |
| `delete_issue` | Delete issue | `issue_id` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Linear action to Agent
-----
#### 1. Go to [`Linear Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/linear/linear.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get an issue:
```python
async def issue(self, id: str) -> GraphQLResponse:
    ...
```

#### 2. Add the tool in this [`Linear Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/linear/linear.py) like below:
```python
@tool(
    app_name="linear",
    tool_name="get_issue",
    description="Get issue by ID",
    parameters=[
        ToolParameter(
            name="issue_id",
            type=ParameterType.STRING,
            description="The ID of the issue to get",
            required=True
        )
    ]
)
def get_issue(self, issue_id: str) -> Tuple[bool, str]:
    """Get issue by ID"""
    """
    Args:
        issue_id: The ID of the issue to get
    Returns:
        Tuple[bool, str]: True if successful, False otherwise
    """
    try:
        # Use LinearDataSource method
        response = self._run_async(self.client.issue(id=issue_id))

        if response.success:
            return True, json.dumps({"data": response.data})
        else:
            return False, json.dumps({"error": response.message})
    except Exception as e:
        logger.error(f"Error getting issue: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `linear`
- `tool_name` - intent of the action, e.g. `create_issue`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


