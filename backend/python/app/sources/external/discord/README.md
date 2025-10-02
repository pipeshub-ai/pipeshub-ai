# Discord Data Source Integration

This module provides integration with Discord using the official `discord.py` SDK.

## Files

- `discord.py` - Main Discord data source implementation with API methods
- `example.py` - Example usage demonstrating how to use the Discord client and data source

## Features

The Discord data source provides the following functionality:

### API Methods

1. **get_guilds()** - Get all guilds (servers) the bot has access to
2. **get_guild(guild_id)** - Get specific guild details
3. **get_channels(guild_id, channel_type)** - Get all channels in a guild (optionally filtered by type)
4. **get_channel(channel_id)** - Get specific channel details
5. **get_messages(channel_id, limit, before, after)** - Get messages from a channel with pagination
6. **get_members(guild_id, limit)** - Get members of a guild
7. **get_member(guild_id, user_id)** - Get specific member details
8. **get_user(user_id)** - Get user information
9. **search_messages(guild_id, query, channel_id, limit)** - Search for messages in a guild
10. **get_guild_roles(guild_id)** - Get all roles in a guild

### Response Format

All methods return a `DiscordResponse` object with the following structure:

```python
@dataclass
class DiscordResponse:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None
```

## Setup

### Prerequisites

1. Create a Discord bot application at [Discord Developer Portal](https://discord.com/developers/applications)
2. Enable the following intents in the bot settings:
   - SERVER MEMBERS INTENT
   - MESSAGE CONTENT INTENT
   - GUILDS INTENT
3. Get your bot token from the Bot section
4. Invite the bot to your server with appropriate permissions

### Installation

The `discord.py` dependency is already added to `pyproject.toml`:

```toml
"discord.py==2.6.3"
```

Install dependencies:
```bash
pip install -e .
```

## Usage

### Basic Example

```python
import asyncio
import os
from app.sources.client.discord.discord import DiscordClient, DiscordTokenConfig
from app.sources.external.discord.discord import DiscordDataSource

async def main():
    # Create Discord client
    token = os.getenv("DISCORD_BOT_TOKEN")
    discord_client = DiscordClient.build_with_config(
        DiscordTokenConfig(token=token)
    )
    
    # Initialize data source
    discord_data_source = DiscordDataSource(discord_client)
    
    # Start the bot
    client = discord_client.get_discord_client()
    asyncio.create_task(client.start(token))
    await client.wait_until_ready()
    
    # Get guilds
    guilds_response = await discord_data_source.get_guilds()
    if guilds_response.success:
        print(f"Found {guilds_response.data['count']} guilds")
        for guild in guilds_response.data['items']:
            print(f"  - {guild['name']}")
    
    await client.close()

asyncio.run(main())
```

### Running the Example

Set your Discord bot token as an environment variable:

```bash
export DISCORD_BOT_TOKEN="your_bot_token_here"
```

Run the example:

```bash
cd backend/python
python -m app.sources.external.discord.example
```

## Authentication

The Discord integration uses **Bot Token** authentication:

```python
DiscordTokenConfig(token="your_bot_token_here")
```

## Architecture

This implementation follows the same pattern as the Slack integration:

1. **Client Layer** (`app/sources/client/discord/discord.py`)
   - `DiscordClient` - Wrapper around discord.py SDK
   - `DiscordRESTClientViaToken` - Token-based authentication
   - `DiscordTokenConfig` - Configuration dataclass
   - `DiscordResponse` - Standardized response wrapper

2. **Data Source Layer** (`app/sources/external/discord/discord.py`)
   - `DiscordDataSource` - API methods for Discord operations
   - Response handling and error management
   - Discord object to dictionary conversion

## Error Handling

All methods include comprehensive error handling:

```python
response = await discord_data_source.get_guild(guild_id)
if response.success:
    # Handle successful response
    guild_data = response.data
else:
    # Handle error
    print(f"Error: {response.error}")
```

## Notes

- Requires Discord bot intents to be properly configured
- Message search is client-side (Discord API doesn't provide native search for bots)
- All Discord objects are converted to dictionaries for JSON serialization
- Async/await pattern is used throughout for Discord API calls

## Requirements

- Python 3.10+
- discord.py 2.6.3
- Valid Discord bot token
- Bot must be invited to servers with appropriate permissions

## Contributing

Follow the existing code patterns and ensure:
1. All code passes `ruff` linting
2. Methods follow snake_case naming convention
3. All responses use `DiscordResponse` wrapper
4. Proper error handling is implemented
5. Documentation is updated
