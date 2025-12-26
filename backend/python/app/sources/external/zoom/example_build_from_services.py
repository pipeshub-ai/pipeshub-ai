# ruff: noqa
"""
Comprehensive Zoom API Example using build_from_services

This example demonstrates all Zoom API categories:
- User Management
- Meeting Management
- Webinar Management
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
"""
import asyncio

from app.sources.client.zoom.zoom import ZoomClient
from app.sources.external.zoom.zoom import ZoomDataSource
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore


async def test_api(
    name: str,
    api_call,
    *args,
    **kwargs,
) -> tuple[bool, dict | None]:
    """Helper function to test an API call with error handling."""
    try:
        response = await api_call(*args, **kwargs)
        if response.status < 400:
            data = response.json() if response.text else None
            print(f"✅ {name}: Success (Status: {response.status})")
            return True, data
        else:
            print(f"⚠️  {name}: API Error (Status: {response.status}) - {response.text[:100]}")
            return False, None
    except Exception as e:
        print(f"❌ {name}: Exception - {str(e)[:100]}")
        return False, None


async def main() -> None:
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    print("=" * 80)
    print("Zoom API Comprehensive Example - build_from_services")
    print("=" * 80)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build Zoom client using configuration service (await the async method)
    try:
        zoom_client = await ZoomClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"\n✅ Zoom client created successfully: {zoom_client}")
    except Exception as e:
        logger.error(f"Failed to create Zoom client: {e}")
        print(f"❌ Error creating Zoom client: {e}")
        return
    
    # Create data source
    zoom_data_source = ZoomDataSource(zoom_client)
    print("\n✅ Zoom data source created\n")

    # Get a user_id for APIs that require it
    user_id = None
    current_user_id = None

    # ========================================================================
    # USER APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("1. USER APIs")
    print("=" * 80)

    # List users
    success, users_data = await test_api(
        "list_users",
        zoom_data_source.list_users,
        page_size=10,
    )
    if success and users_data:
        users = users_data.get("users", [])
        if users:
            user_id = users[0].get("id")
            print(f"   Found {len(users)} users, using first user_id: {user_id}")

    # Get current user (me)
    success, current_user_data = await test_api(
        "get_user (me)",
        zoom_data_source.get_user,
        user_id="me",
    )
    if success and current_user_data:
        current_user_id = current_user_data.get("id")
        print(f"   Current user ID: {current_user_id}, Email: {current_user_data.get('email', 'N/A')}")

    # Use current_user_id if available, otherwise use user_id
    test_user_id = current_user_id or user_id or "me"

    # ========================================================================
    # MEETING APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("2. MEETING APIs")
    print("=" * 80)

    # List meetings
    await test_api(
        "list_meetings",
        zoom_data_source.list_meetings,
        user_id=test_user_id,
        page_size=5,
    )

    # Get meeting (requires a meeting_id - will likely fail without one)
    # This is just to demonstrate the API call
    await test_api(
        "get_meeting (example - requires valid meeting_id)",
        zoom_data_source.get_meeting,
        meeting_id="example_meeting_id",
    )

    # Create meeting
    meeting_info = {
        "topic": "Test Meeting from build_from_services",
        "type": 2,  # Scheduled meeting
        "duration": 30,
        "settings": {
            "host_video": True,
            "participant_video": True,
        },
    }
    success, meeting_data = await test_api(
        "create_meeting",
        zoom_data_source.create_meeting,
        user_id=test_user_id,
        meeting_info=meeting_info,
    )
    created_meeting_id = None
    if success and meeting_data:
        created_meeting_id = meeting_data.get("id")
        print(f"   Created meeting ID: {created_meeting_id}")

    # Update meeting (if we created one)
    if created_meeting_id:
        update_info = {"topic": "Updated Test Meeting"}
        await test_api(
            "update_meeting",
            zoom_data_source.update_meeting,
            meeting_id=created_meeting_id,
            meeting_info=update_info,
        )

    # ========================================================================
    # WEBINAR APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("3. WEBINAR APIs")
    print("=" * 80)

    # List webinars
    await test_api(
        "list_webinars",
        zoom_data_source.list_webinars,
        user_id=test_user_id,
        page_size=5,
    )

    # Get webinar (requires a webinar_id)
    await test_api(
        "get_webinar (example - requires valid webinar_id)",
        zoom_data_source.get_webinar,
        webinar_id="example_webinar_id",
    )

    # Create webinar
    webinar_info = {
        "topic": "Test Webinar from build_from_services",
        "type": 5,  # Webinar
        "duration": 60,
        "settings": {
            "host_video": True,
            "panelists_video": True,
        },
    }
    await test_api(
        "create_webinar",
        zoom_data_source.create_webinar,
        user_id=test_user_id,
        webinar_info=webinar_info,
    )

    # ========================================================================
    # TEAM CHAT APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("4. TEAM CHAT APIs")
    print("=" * 80)

    # List chat channels
    await test_api(
        "list_chat_channels",
        zoom_data_source.list_chat_channels,
        user_id=test_user_id,
    )

    # Send chat message (requires channel info)
    message_info = {
        "message": "Test message from build_from_services",
        "to_channel": "example_channel_id",
    }
    await test_api(
        "send_chat_message (example - requires valid channel)",
        zoom_data_source.send_chat_message,
        user_id=test_user_id,
        message_info=message_info,
    )

    # ========================================================================
    # PHONE APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("5. PHONE APIs")
    print("=" * 80)

    # List phone users
    await test_api(
        "list_phone_users",
        zoom_data_source.list_phone_users,
    )

    # Get phone user
    await test_api(
        "get_phone_user",
        zoom_data_source.get_phone_user,
        user_id=test_user_id,
    )

    # ========================================================================
    # MAIL APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("6. MAIL APIs")
    print("=" * 80)

    # List mail messages
    await test_api(
        "list_mail_messages",
        zoom_data_source.list_mail_messages,
        user_id=test_user_id,
    )

    # Send mail message
    mail_info = {
        "to": ["example@example.com"],
        "subject": "Test Email",
        "body": "Test email body",
    }
    await test_api(
        "send_mail_message (example)",
        zoom_data_source.send_mail_message,
        user_id=test_user_id,
        message_info=mail_info,
    )

    # ========================================================================
    # CALENDAR APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("7. CALENDAR APIs")
    print("=" * 80)

    # List calendar events
    await test_api(
        "list_calendar_events",
        zoom_data_source.list_calendar_events,
        user_id=test_user_id,
    )

    # Create calendar event
    event_info = {
        "summary": "Test Calendar Event",
        "start": {"dateTime": "2024-12-31T10:00:00Z"},
        "end": {"dateTime": "2024-12-31T11:00:00Z"},
    }
    await test_api(
        "create_calendar_event",
        zoom_data_source.create_calendar_event,
        user_id=test_user_id,
        event_info=event_info,
    )

    # ========================================================================
    # SCHEDULER APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("8. SCHEDULER APIs")
    print("=" * 80)

    # List scheduler availability
    await test_api(
        "list_scheduler_availability",
        zoom_data_source.list_scheduler_availability,
        user_id=test_user_id,
    )

    # Create scheduler booking
    booking_info = {
        "start_time": "2024-12-31T10:00:00Z",
        "duration": 30,
        "timezone": "UTC",
    }
    await test_api(
        "create_scheduler_booking",
        zoom_data_source.create_scheduler_booking,
        user_id=test_user_id,
        booking_info=booking_info,
    )

    # ========================================================================
    # ROOMS APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("9. ROOMS APIs")
    print("=" * 80)

    # List rooms
    await test_api(
        "list_rooms",
        zoom_data_source.list_rooms,
    )

    # Get room (requires room_id)
    await test_api(
        "get_room (example - requires valid room_id)",
        zoom_data_source.get_room,
        room_id="example_room_id",
    )

    # ========================================================================
    # CLIPS APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("10. CLIPS APIs")
    print("=" * 80)

    # List clips
    await test_api(
        "list_clips",
        zoom_data_source.list_clips,
        user_id=test_user_id,
    )

    # Get clip (requires clip_id)
    await test_api(
        "get_clip (example - requires valid clip_id)",
        zoom_data_source.get_clip,
        clip_id="example_clip_id",
    )

    # ========================================================================
    # WHITEBOARD APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("11. WHITEBOARD APIs")
    print("=" * 80)

    # List whiteboards
    await test_api(
        "list_whiteboards",
        zoom_data_source.list_whiteboards,
        user_id=test_user_id,
    )

    # Create whiteboard
    whiteboard_info = {
        "name": "Test Whiteboard",
        "description": "Test whiteboard from build_from_services",
    }
    await test_api(
        "create_whiteboard",
        zoom_data_source.create_whiteboard,
        user_id=test_user_id,
        whiteboard_info=whiteboard_info,
    )

    # ========================================================================
    # CALL RECORDING (CRC) APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("12. CALL RECORDING (CRC) APIs")
    print("=" * 80)

    # List call recordings
    await test_api(
        "list_call_recordings",
        zoom_data_source.list_call_recordings,
    )

    # Get meeting recordings (requires meeting_id)
    if created_meeting_id:
        await test_api(
            "get_meeting_recordings",
            zoom_data_source.get_meeting_recordings,
            meeting_id=created_meeting_id,
        )
    else:
        await test_api(
            "get_meeting_recordings (example - requires valid meeting_id)",
            zoom_data_source.get_meeting_recordings,
            meeting_id="example_meeting_id",
        )

    # ========================================================================
    # CHATBOT APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("13. CHATBOT APIs")
    print("=" * 80)

    # List chatbots
    await test_api(
        "list_chatbots",
        zoom_data_source.list_chatbots,
    )

    # Send chatbot message
    chatbot_message_info = {
        "message": "Hello from build_from_services",
        "chatbot_id": "example_chatbot_id",
    }
    await test_api(
        "send_chatbot_message (example - requires valid chatbot_id)",
        zoom_data_source.send_chatbot_message,
        chatbot_id="example_chatbot_id",
        message_info=chatbot_message_info,
    )

    # ========================================================================
    # AI COMPANION APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("14. AI COMPANION APIs")
    print("=" * 80)

    # Get AI Companion summary (requires meeting_id)
    if created_meeting_id:
        await test_api(
            "get_ai_companion_summary",
            zoom_data_source.get_ai_companion_summary,
            meeting_id=created_meeting_id,
        )
    else:
        await test_api(
            "get_ai_companion_summary (example - requires valid meeting_id)",
            zoom_data_source.get_ai_companion_summary,
            meeting_id="example_meeting_id",
        )

    # Get AI Companion insights
    await test_api(
        "get_ai_companion_insights",
        zoom_data_source.get_ai_companion_insights,
        user_id=test_user_id,
    )

    # ========================================================================
    # ZOOM DOCS APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("15. ZOOM DOCS APIs")
    print("=" * 80)

    # List documents
    await test_api(
        "list_documents",
        zoom_data_source.list_documents,
        user_id=test_user_id,
    )

    # Create document
    document_info = {
        "name": "Test Document",
        "content": "Test document content from build_from_services",
    }
    await test_api(
        "create_document",
        zoom_data_source.create_document,
        user_id=test_user_id,
        document_info=document_info,
    )

    # ========================================================================
    # ACCOUNTS APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("16. ACCOUNTS APIs")
    print("=" * 80)

    # List accounts
    success, accounts_data = await test_api(
        "list_accounts",
        zoom_data_source.list_accounts,
    )
    account_id = None
    if success and accounts_data:
        accounts = accounts_data.get("accounts", [])
        if accounts:
            account_id = accounts[0].get("id")
            print(f"   Found {len(accounts)} accounts, using first account_id: {account_id}")

    # Get account
    if account_id:
        await test_api(
            "get_account",
            zoom_data_source.get_account,
            account_id=account_id,
        )
    else:
        await test_api(
            "get_account (example - requires valid account_id)",
            zoom_data_source.get_account,
            account_id="example_account_id",
        )

    # ========================================================================
    # SCIM 2 APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("17. SCIM 2 APIs")
    print("=" * 80)

    # List SCIM users
    await test_api(
        "list_scim_users",
        zoom_data_source.list_scim_users,
    )

    # Create SCIM user
    scim_user_info = {
        "userName": "testuser@example.com",
        "name": {
            "givenName": "Test",
            "familyName": "User",
        },
        "emails": [{"value": "testuser@example.com", "primary": True}],
    }
    await test_api(
        "create_scim_user",
        zoom_data_source.create_scim_user,
        user_info=scim_user_info,
    )

    # ========================================================================
    # QSS (Quality Service Score) APIs
    # ========================================================================
    print("\n" + "=" * 80)
    print("18. QSS (Quality Service Score) APIs")
    print("=" * 80)

    # Get QSS report
    await test_api(
        "get_qss_report",
        zoom_data_source.get_qss_report,
    )

    # ========================================================================
    # SUMMARY
    # ========================================================================
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("\n✅ Comprehensive Zoom API example completed!")
    print("   All 18 API categories have been demonstrated.")
    print("\nNote: Some API calls may fail due to:")
    print("   - Missing required permissions/scopes")
    print("   - Missing required IDs (meeting_id, webinar_id, etc.)")
    print("   - Account plan limitations")
    print("   - Feature availability")
    print("\nThis is expected behavior - the example demonstrates")
    print("how to call all available Zoom APIs using build_from_services.")


if __name__ == "__main__":
    asyncio.run(main())
