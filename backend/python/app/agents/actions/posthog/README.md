# PostHog Connector
## Overview
[`PostHog`](https://posthog.com/) is an open‑source product analytics platform that lets you capture events, analyze user behavior, and build features like feature flags and session replays.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/posthog.png" alt="PostHog Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`PostHog Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/posthog/posthog.py) - creates PostHog client.
<!--([`Local`](/backend/python/app/sources/client/posthog/posthog.py))-->

- [`PostHog APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/posthog/posthog.py) - provides methods to connect to PostHog APIs.
<!--([`Local`](/backend/python/app/sources/external/posthog/posthog.py))-->

- [`PostHog Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/posthog/posthog.py) - actions that AI agents can do on PostHog (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/posthog/posthog.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `capture_event` | Capture an event | `event`, `distinct_id`, `properties (Optional)`, `timestamp (Optional)` |
| `get_event` | Get an event by ID | `event_id` |
| `get_person` | Get a person by ID | `person_id` |
| `update_person` | Update person properties | `person_id`, `properties` |
| `delete_person` | Delete a person | `person_id` |
| `search_events` | Search events with filters | `after (Optional)`, `before (Optional)`, `distinct_id (Optional)`, `event (Optional)`, `properties (Optional)`, `limit (Optional)` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new PostHog action to Agent
-----
#### 1. Go to [`PostHog Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/posthog/posthog.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to get an event:
```python
async def event(
    self,
    id: str
) -> PostHogResponse:
    ...
```

#### 2. Add the tool in this [`PostHog Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/posthog/posthog.py) like below:
```python
@tool(
    app_name="posthog",
    tool_name="get_event",
    description="Get a single event by ID from PostHog",
    parameters=[
        ToolParameter(
            name="event_id",
            type=ParameterType.STRING,
            description="Event ID"
        )
    ],
    returns="JSON with event data"
)
def get_event(self, event_id: str) -> Tuple[bool, str]:
    """Get a single event by ID from PostHog."""
    try:
        response = self._run_async(
            self.client.event(id=event_id)
        )
        return self._handle_response(response, "Event retrieved successfully")
    except Exception as e:
        logger.error(f"Error getting event: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `posthog`
- `tool_name` - intent of the action, e.g. `capture_event`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


