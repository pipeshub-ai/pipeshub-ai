# Zendesk Connector
## Overview
[`Zendesk`](https://zendesk.com/) is a customer service solution that provides ticketing, knowledge base, and customer support tools.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/zendesk.png" alt="Zendesk Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Zendesk Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/zendesk/zendesk.py) - creates Zendesk client.
<!--([`Local`](/backend/python/app/sources/client/zendesk/zendesk.py))-->

- [`Zendesk APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/zendesk/zendesk.py) - provides methods to connect to Zendesk APIs.
<!--([`Local`](/backend/python/app/sources/external/zendesk/zendesk.py))-->

- [`Zendesk Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/zendesk/zendesk.py) - actions that AI agents can do on Zendesk (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/zendesk/zendesk.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `get_current_user` | Get current user information | - |
| `list_tickets` | List tickets | `sort_by (Optional)`, `sort_order (Optional)`, `per_page (Optional)`, `page (Optional)` |
| `get_ticket` | Get a specific ticket | `ticket_id` |
| `create_ticket` | Create a ticket | `subject`, `description`, `requester_id (Optional)`, `assignee_id (Optional)`, `priority (Optional)`, `status (Optional)` |
| `update_ticket` | Update a ticket | `ticket_id`, `subject (Optional)`, `description (Optional)`, `assignee_id (Optional)`, `priority (Optional)`, `status (Optional)` |
| `delete_ticket` | Delete a ticket | `ticket_id` |
| `list_users` | List users | `role (Optional)`, `include (Optional)` |
| `get_user` | Get a specific user | `user_id` |
| `search_tickets` | Search tickets | `query`, `sort_by (Optional)`, `sort_order (Optional)` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Zendesk action to Agent
-----
#### 1. Go to [`Zendesk Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/zendesk/zendesk.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to create a ticket:
```python
async def create_ticket(self, ticket: Dict[str, Any]) -> HTTPResponse:
    ...
```

#### 2. Add the tool in this [`Zendesk Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/zendesk/zendesk.py) like below:
```python
@tool(
    app_name="zendesk",
    tool_name="create_ticket",
    description="Create a new ticket",
    parameters=[
        ToolParameter(name="subject", type=ParameterType.STRING, description="Subject"),
        ToolParameter(name="description", type=ParameterType.STRING, description="Description"),
    ],
)
def create_ticket(self, subject: str, description: str) -> Tuple[bool, str]:
    payload = {"ticket": {"subject": subject, "description": description}}
    resp = self._run_async(self.client.create_ticket(ticket=payload))
    return (True, resp.to_json()) if resp.success else (False, resp.to_json())
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `zendesk`
- `tool_name` - intent of the action, e.g. `create_ticket`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


