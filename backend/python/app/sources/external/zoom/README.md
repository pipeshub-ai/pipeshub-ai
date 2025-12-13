# Zoom Data Source

This module provides a Python client for interacting with the Zoom API. It supports both OAuth (Server-to-Server) and access token authentication methods.

## Overview

The Zoom integration consists of:

1. **ZoomClient** (`app/sources/client/zoom/zoom.py`): HTTP client wrapper that handles authentication
   - `ZoomRESTClientViaToken`: Uses access token authentication
   - `ZoomRESTClientViaOAuth`: Uses Server-to-Server OAuth authentication

2. **ZoomDataSource** (`app/sources/external/zoom/zoom.py`): High-level API wrapper with methods for:
   - User management
   - Meeting management
   - Webinar management
   - Team Chat
   - Phone
   - Mail
   - Calendar
   - Scheduler
   - Rooms
   - Clips
   - Whiteboard
   - Call Recording (CRC)
   - Chatbot
   - AI Companion
   - Zoom Docs
   - Accounts
   - SCIM 2
   - QSS (Quality Service Score)

## Setup

### Prerequisites

1. A Zoom account with API access
2. Either:
   - An OAuth app created in the [Zoom App Marketplace](https://marketplace.zoom.us/)
   - OR an access token for direct API access

### Environment Variables

For **OAuth authentication** (Server-to-Server OAuth):
```bash
export ZOOM_CLIENT_ID="your_client_id"
export ZOOM_CLIENT_SECRET="your_client_secret"
export ZOOM_ACCOUNT_ID="your_account_id"
export ZOOM_REDIRECT_URI="http://localhost:3001/connectors/oauth/callback/Zoom"  # Optional
```

For **Token authentication**:
```bash
export ZOOM_ACCESS_TOKEN="your_access_token"
```

### Creating a Zoom OAuth App

1. Go to [Zoom App Marketplace](https://marketplace.zoom.us/)
2. Click "Develop" → "Build App"
3. Select "Server-to-Server OAuth" app type
4. Fill in app details and create the app
5. Note down:
   - **Account ID** (found on the App Credentials page)
   - **Client ID** (found on the App Credentials page)
   - **Client Secret** (found on the App Credentials page)
6. Add required scopes in the "Scopes" section (e.g., `meeting:write:admin`, `user:read:admin`)

## Testing

### Testing with Access Token

1. Get an access token:
   - Create a Server-to-Server OAuth app in Zoom App Marketplace
   - Use the token endpoint to get an access token, or
   - Use the OAuth client's `get_access_token_via_server_to_server()` method

2. Set environment variable:
   ```bash
   export ZOOM_ACCESS_TOKEN="your_token"
   ```

3. Run the example:
   ```bash
   cd backend/python
   python3 -m app.sources.external.zoom.example
   ```

### Testing with OAuth

1. Create a Zoom OAuth app (see "Creating a Zoom OAuth App" above)

2. Set environment variables:
   ```bash
   export ZOOM_CLIENT_ID="your_client_id"
   export ZOOM_CLIENT_SECRET="your_client_secret"
   export ZOOM_ACCOUNT_ID="your_account_id"
   ```

3. Run the example:
   ```bash
   cd backend/python
   python3 -m app.sources.external.zoom.example
   ```

### Testing Individual Services

The example script (`example.py`) demonstrates how to test various Zoom services. You can modify it to test specific features:

- **Users**: Test user listing and retrieval
- **Meetings**: Test meeting creation, listing, and management
- **Team Chat**: Test chat channel listing and messaging
- **Phone**: Test phone user management
- **Mail**: Test mail message operations
- **Calendar**: Test calendar event management
- **Scheduler**: Test scheduler availability and bookings
- **Rooms**: Test Zoom Rooms management
- **Clips**: Test clip listing and retrieval
- **Whiteboard**: Test whiteboard operations
- **Call Recording**: Test recording retrieval
- **Chatbot**: Test chatbot interactions
- **AI Companion**: Test AI summaries and insights
- **Zoom Docs**: Test document management
- **Accounts**: Test account management
- **SCIM 2**: Test SCIM user provisioning
- **QSS**: Test quality service score reports

## Available API Methods

### User APIs
- `list_users()`: List all users on your account
- `get_user(user_id)`: Get user information
- `create_user(action, user_info)`: Create a new user

### Meeting APIs
- `list_meetings(user_id)`: List all meetings for a user
- `get_meeting(meeting_id)`: Get meeting details
- `create_meeting(user_id, meeting_info)`: Create a new meeting
- `update_meeting(meeting_id, meeting_info)`: Update meeting details
- `delete_meeting(meeting_id)`: Delete a meeting

### Webinar APIs
- `list_webinars(user_id)`: List all webinars for a user
- `get_webinar(webinar_id)`: Get webinar details
- `create_webinar(user_id, webinar_info)`: Create a new webinar

### Team Chat APIs
- `list_chat_channels(user_id)`: List user's chat channels
- `send_chat_message(user_id, message_info)`: Send a chat message

### Phone APIs
- `list_phone_users()`: List phone users
- `get_phone_user(user_id)`: Get phone user details

### Mail APIs
- `list_mail_messages(user_id)`: List mail messages
- `send_mail_message(user_id, message_info)`: Send a mail message

### Calendar APIs
- `list_calendar_events(user_id)`: List calendar events
- `create_calendar_event(user_id, event_info)`: Create a calendar event

### Scheduler APIs
- `list_scheduler_availability(user_id)`: List scheduler availability
- `create_scheduler_booking(user_id, booking_info)`: Create a scheduler booking

### Rooms APIs
- `list_rooms()`: List Zoom Rooms
- `get_room(room_id)`: Get Zoom Room details

### Clips APIs
- `list_clips(user_id)`: List user's clips
- `get_clip(clip_id)`: Get clip details

### Whiteboard APIs
- `list_whiteboards(user_id)`: List user's whiteboards
- `create_whiteboard(user_id, whiteboard_info)`: Create a whiteboard

### Call Recording (CRC) APIs
- `list_call_recordings()`: List call recordings
- `get_meeting_recordings(meeting_id)`: Get meeting recordings

### Chatbot APIs
- `list_chatbots()`: List chatbots
- `send_chatbot_message(chatbot_id, message_info)`: Send a chatbot message

### AI Companion APIs
- `get_ai_companion_summary(meeting_id)`: Get AI Companion meeting summary
- `get_ai_companion_insights(user_id)`: Get AI Companion insights

### Zoom Docs APIs
- `list_documents(user_id)`: List user's documents
- `create_document(user_id, document_info)`: Create a document

### Accounts APIs
- `list_accounts()`: List accounts
- `get_account(account_id)`: Get account details

### SCIM 2 APIs
- `list_scim_users()`: List SCIM users
- `create_scim_user(user_info)`: Create a SCIM user

### QSS APIs
- `get_qss_report()`: Get QSS report

## Troubleshooting

### "Invalid auth type" error
- Ensure your environment variables are set correctly
- Check that `authType` in config is either "OAUTH" or "TOKEN"

### "Token exchange failed" error
- Verify your `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, and `ZOOM_ACCOUNT_ID` are correct
- Ensure your OAuth app has the required scopes
- Check that your account has API access enabled

### "HTTP client is not initialized" error
- Ensure you're calling `get_access_token_via_server_to_server()` for OAuth clients before using the data source

### 401 Unauthorized errors
- Your access token may have expired (Server-to-Server OAuth tokens typically last 1 hour)
- Regenerate the token or use OAuth client which handles token refresh

### API endpoint not found (404 errors)
- Some features may require specific Zoom plan tiers
- Verify that your account has access to the feature you're trying to use
- Check the Zoom API documentation for feature availability

## References

- [Zoom API Documentation](https://marketplace.zoom.us/docs/api-reference/zoom-api/)
- [Zoom Server-to-Server OAuth Guide](https://marketplace.zoom.us/docs/guides/build/server-to-server-oauth-app/)
- [Zoom OpenAPI Spec](https://raw.githubusercontent.com/zoom/developer-api/main/openapi.json)
