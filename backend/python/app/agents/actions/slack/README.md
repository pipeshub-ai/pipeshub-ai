# Slack Connector
## Overview
[`Slack`](https://slack.com/) is a messaging platform for teams that supports channels, direct messages, and rich integrations via APIs.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/slack.png" alt="Slack Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Slack Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/slack/slack.py) - creates Slack client.
<!--([`Local`](/backend/python/app/sources/client/slack/slack.py))-->

- [`Slack APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/slack/slack.py) - provides methods to connect to Slack APIs.
<!--([`Local`](/backend/python/app/sources/external/slack/slack.py))-->

- [`Slack Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/slack/slack.py) - actions that AI agents can do on Slack (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/slack/slack.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `send_message` | Send a message to a channel | `channel`, `message` |
| `get_channel_history` | Fetch channel message history (with mention resolution) | `channel`, `limit (Optional)` |
| `get_channel_info` | Get channel info | `channel` |
| `get_user_info` | Get user info | `user` |
| `fetch_channels` | List channels | - |
| `search_all` | Search messages/files/channels | `query`, `limit (Optional)` |
| `get_channel_members` | Get members of a channel | `channel` |
| `get_channel_members_by_id` | Get members by channel ID | `channel_id` |
| `resolve_user` | Resolve user ID to display name and email | `user_id` |
| `check_token_info` | Check token scopes/info | - |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Slack action to Agent
-----
#### 1. Go to [`Slack Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/slack/slack.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to send a message:
```python
async def chat_me_message(self,
    *,
    channel: Optional[str] = None,
    text: Optional[str] = None,
    **kwargs
) -> SlackResponse:
    ...
```

#### 2. Add the tool in this [`Slack Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/slack/slack.py) like below:
```python
@tool(
    app_name="slack",
    tool_name="send_message",
    parameters=[
        ToolParameter(
            name="channel",
            type=ParameterType.STRING,
            description="The channel to send the message to",
            required=True
        ),
        ToolParameter(
            name="message",
            type=ParameterType.STRING,
            description="The message to send",
            required=True
        )
    ]
)
def send_message(self, channel: str, message: str) -> Tuple[bool, str]:
    """Send a message to a channel"""
    """
    Args:
        channel: The channel to send the message to
        message: The message to send
    Returns:
        A tuple with a boolean indicating success/failure and a JSON string with the message details
    """
    try:
        # Use SlackDataSource method
        response = self._run_async(self.client.chat_me_message(
            channel=channel,
            text=message
        ))
        slack_response = self._handle_slack_response(response)
        return (slack_response.success, slack_response.to_json())
    except Exception as e:
        # Explicitly surface membership errors without side-effects
        if "not_in_channel" in str(e):
            err = SlackResponse(success=False, error="not_in_channel")
            return (err.success, err.to_json())
        logger.error(f"Error in send_message: {e}")
        slack_response = self._handle_slack_error(e)
        return (slack_response.success, slack_response.to_json())

```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `slack`
- `tool_name` - intent of the action, e.g. `send_message`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type


