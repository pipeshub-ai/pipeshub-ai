# Google Meet Connector
## Overview
[`Google Meet`](https://workspace.google.com/products/meet/) provides secure video meetings for teams and businesses.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/google.png" alt="Google Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Google Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/google/google.py) - creates Google client.
<!--([`Local`](/backend/python/app/sources/client/google/google.py))-->

- [`Google Meet APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/google/meet/meet.py) - provides methods to connect to Google Meet APIs.
<!--([`Local`](/backend/python/app/sources/external/google/meet/meet.py))-->

- [`Google Meet Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/google/meet/meet.py) - actions that AI agents can do on Google Meet (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/google/meet/meet.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `create_meeting_space` | Create a new Meet space | `space_config (Optional, object)` |
| `get_meeting_space` | Get details of a Meet space | `space_name` |
| `end_active_conference` | End an active conference in a space | `space_name` |
| `get_conference_records` | List conference records | `page_size (Optional)`, `page_token (Optional)`, `filter (Optional)`, `start_time_from (Optional)`, `start_time_to (Optional)` |
| `get_conference_participants` | List participants for a record | `conference_record`, `page_size (Optional)`, `page_token (Optional)`, `filter (Optional)` |
| `get_conference_recordings` | List recordings for a record | `conference_record`, `page_size (Optional)`, `page_token (Optional)` |
| `get_conference_transcripts` | List transcripts for a record | `conference_record`, `page_size (Optional)`, `page_token (Optional)` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.


<br></br>
### How to expose new Google Meet action to Agent
-----
#### 1. Go to [`Google Meet Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/google/meet/meet.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to create a meeting space:
```python
async def spaces_create(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ...
```

#### 2. Add the tool in this [`Google Meet Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/google/meet/meet.py) like below:
```python
@tool(
    app_name="meet",
    tool_name="create_meeting_space",
    parameters=[
        ToolParameter(
            name="space_config",
            type=ParameterType.OBJECT,
            description="Optional space configuration body to pass to Meet API",
            required=False
        )
    ]
)
def create_meeting_space(self, space_config: Optional[dict] = None) -> tuple[bool, str]:
    """Create a new Google Meet space"""
    """
    Returns:
        tuple[bool, str]: True if successful, False otherwise
    """
    try:
        space = self._run_async(self.client.spaces_create())

        return True, json.dumps({
            "space_name": space.get("name", ""),
            "meeting_code": space.get("meetingCode", ""),
            "meeting_uri": space.get("meetingUri", ""),
            "space_config": space.get("spaceConfig", {}),
            "message": "Meeting space created successfully"
        })
    except Exception as e:
        logger.error(f"Failed to create meeting space: {e}")
        return False, json.dumps({"error": str(e)}
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `google_meet`
- `tool_name` - intent of the action, e.g. `create_meeting`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type
