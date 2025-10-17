# Discord Connector
## Overview
[`Discord`](https://discord.com/) is a communication platform featuring voice, video, and text. Bots can automate tasks like sending messages, managing channels, and retrieving server information.

<br></br>
<br></br>
<div align="center">
  <img src="https://raw.githubusercontent.com/pipeshub-ai/documentation/refs/heads/main/logo/discord.png" alt="Discord Logo" width="200"/>
</div>


<br></br>
## PipesHub Actions 

It has three distinct layers:
- [`Discord Client`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/client/discord/discord.py) - creates Discord client.
<!--([`Local`](/backend/python/app/sources/client/discord/discord.py))-->

- [`Discord APIs`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/discord/discord.py) - provides methods to connect to Discord APIs.
<!--([`Local`](/backend/python/app/sources/external/discord/discord.py))-->

- [`Discord Actions`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/discord/discord.py) - actions that AI agents can do on Discord (marked with the `@tool` decorator)
<!--([`Local`](/backend/python/app/agents/actions/discord/discord.py))-->

<br></br>
### Supported Actions
-----
Here's what's available out of the box:
| Tool | What It Does | Parameters |
|------|---------------|------------|
| `send_message` | Send a message to a text channel | `channel_id`, `content` |
| `get_channel` | Get channel information | `channel_id` |
| `create_channel` | Create a channel in a guild | `guild_id`, `name`, `channel_type (Optional)` |
| `delete_channel` | Delete a channel | `channel_id` |
| `get_messages` | Get messages in a channel | `channel_id`, `limit (Optional)`, `before (Optional)`, `after (Optional)` |
| `get_guilds` | Get guilds (servers) | - |
| `get_guild_channels` | Get channels in a guild | `guild_id`, `channel_type (Optional)` |
| `send_direct_message` | Send a DM to a user | `user_id`, `content` |
| `get_guild_members` | Get members in a guild | `guild_id`, `limit (Optional)` |
| `create_role` | Create a role in a guild | `guild_id`, `name` |

**Response:** Every tool returns two things - a boolean indicating success or failure, and a JSON string with the actual data or error details.

<br></br>
### How to expose new Discord action to Agent
-----
#### 1. Go to [`Discord Data Source`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/sources/external/discord/discord.py)
Find the operation (method) you want to expose to the PipesHub Agent. For example, to send a message:
```python
async def send_message(self, channel_id: int, content: str) -> DiscordResponse:
    ...
```

#### 2. Add the tool in this [`Discord Tool`](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/python/app/agents/actions/discord/discord.py) like below:
```python
@tool(
    app_name="discord",
    tool_name="send_message",
    description="Send a message to a Discord text channel",
    parameters=[
        ToolParameter(
            name="channel_id",
            type=ParameterType.NUMBER,
            description="The ID of the channel to send the message to (required)"
        ),
        ToolParameter(
            name="content",
            type=ParameterType.STRING,
            description="The content of the message to send (required)"
        )
    ],
    returns="JSON with sent message details"
)
def send_message(self, channel_id: int, content: str) -> Tuple[bool, str]:
    try:
        response = self._run_async(
            self.client.send_message(channel_id=channel_id, content=content)
        )
        return self._handle_response(response, "Message sent successfully")
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False, json.dumps({"error": str(e)})
```

#### 3. How to decorate the method
- `app_name` - App Name, e.g. `discord`
- `tool_name` - intent of the action, e.g. `send_message`
- `description` - what it does in details, must be descriptive for agent to read
- `parameters` - parameters with type and purpose
- `returns` - expected response type