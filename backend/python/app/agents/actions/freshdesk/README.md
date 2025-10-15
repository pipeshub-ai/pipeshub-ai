# FreshDesk Connector
## Overview
[`Freshdesk`](https://freshdesk.com/) is a cloud‑based customer support platform providing ticketing, knowledge base, and automation tools for support teams.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/freshdesk.png" alt="Freshdesk Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`FreshDesk Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/freshdesk/freshdesk.py) - creates FreshDesk client.
<!--([`Local`](/backend/python/app/sources/client/freshdesk/freshdesk.py))-->

- [`FreshDesk APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/freshdesk/freshdesk.py) - provides methods to connect to FreshDesk APIs.
<!--([`Local`](/backend/python/app/sources/external/freshdesk/freshdesk.py))-->

- [`FreshDesk Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/freshdesk/freshdesk.py) - actions that AI agents can do on FreshDesk (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/freshdesk/freshdesk.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_ticket` | Create a new support ticket | `subject`, `description`, `email`, `priority (Optional)`, `status (Optional)`, `requester_id (Optional)`, `phone (Optional)`, `source (Optional)`, `tags (Optional, array)`, `cc_emails (Optional, array)`, `custom_fields (Optional, object)`, `attachments (Optional, array)` |
| `get_ticket` | Get a ticket by ID | `ticket_id` |
| `update_ticket` | Update an existing ticket | `ticket_id`, `subject (Optional)`, `description (Optional)`, `priority (Optional)`, `status (Optional)`, `tags (Optional, array)`, `custom_fields (Optional, object)` |
| `delete_ticket` | Delete a ticket | `ticket_id` |
| `create_note` | Add a note to a ticket | `ticket_id`, `body`, `private (Optional)`, `notify_emails (Optional, array)` |
| `create_reply` | Add a reply to a ticket | `ticket_id`, `body`, `cc_emails (Optional, array)`, `bcc_emails (Optional, array)` |
| `create_agent` | Create a support agent | `first_name`, `email`, `last_name (Optional)`, `occasional (Optional)`, `job_title (Optional)`, `work_phone_number (Optional)`, `mobile_phone_number (Optional)`, `department_ids (Optional, array)`, `can_see_all_tickets_from_associated_departments (Optional)`, `reporting_manager_id (Optional)`, `address (Optional)`, `time_zone (Optional)`, `time_format (Optional)`, `language (Optional)`, `location_id (Optional)`, `background_information (Optional)`, `scoreboard_level_id (Optional)`, `roles (Optional, array)`, `signature (Optional)`, `custom_fields (Optional, object)`, `workspace_ids (Optional, array)` |
| `search_tickets` | Search tickets (maps to data source `filter_tickets`) | `query`, `page (Optional)` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new FreshDesk action to Agent
-----
#### 1. Go to [`FreshDesk Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/freshdesk/freshdesk.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to Get a ticket:
```python
async def get_ticket(
    self,
    id: int,
    include: Optional[str] = None
) -> FreshDeskResponse:
    ...
```

#### 2. Add the tool in this [`FreshDesk Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/freshdesk/freshdesk.py) like below:
```python
@tool(
    app_name="freshdesk",
    tool_name="get_ticket",
    description="Get details of a specific ticket",
    parameters=[
        ToolParameter(
            name="ticket_id",
            type=ParameterType.NUMBER,
            description="The ID of the ticket to retrieve (required)"
        )
    ],
    returns="JSON with ticket details"
)
def get_ticket(self, ticket_id: int, include: Optional[str] = None) -> Tuple[bool, str]:
    try:
        response = self._run_async(self.client.get_ticket(id=ticket_id, include=include))
        return self._handle_response(response, "Ticket retrieved successfully")
    except Exception as e:
        logger.error(f"Error getting ticket: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `freshdesk`
- `tool_name` - intent of the action, e.g. `create_ticket`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type
