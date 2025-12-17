# ruff: noqa
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent.parent.parent.parent.parent.parent / ".env"
load_dotenv(env_path)

from app.sources.client.zoom.zoom import (
    ZoomClient,
    ZoomOAuthConfig,
    ZoomRESTClientViaOAuth,
    ZoomTokenConfig,
)
from app.sources.external.zoom.zoom import ZoomDataSource


async def example_with_token() -> None:
    """Example using access token authentication"""
    token = os.getenv("ZOOM_ACCESS_TOKEN")
    if not token:
        raise Exception("ZOOM_ACCESS_TOKEN is not set")

    zoom_client: ZoomClient = ZoomClient.build_with_config(
        ZoomTokenConfig(token=token),
    )
    print("Zoom client created with token")
    print(zoom_client)

    zoom_data_source = ZoomDataSource(zoom_client)
    print("Zoom data source created")
    print(zoom_data_source)

    # ------------------------------------------------------------------
    # Accounts → Users
    # ------------------------------------------------------------------
    print("\n=== Listing users ===")
    response = await zoom_data_source.list_users(page_size=10)
    print(f"Status: {response.status}")
    if response.status < 400:
        users_data = response.json()
        print(f"Users: {users_data}")
    else:
        print(f"Error: {response.text}")
        return

    # Use current user for user-scoped APIs
    print("\n=== Getting current user ===")
    response = await zoom_data_source.get_user(user_id="me")
    print(f"Status: {response.status}")
    if response.status < 400:
        user_data = response.json()
        print(f"User: {user_data}")
        current_user_id = user_data.get("id")
    else:
        print(f"Error: {response.text}")
        return

    # ------------------------------------------------------------------
    # Accounts → Accounts
    # ------------------------------------------------------------------
    print("\n=== Listing accounts ===")
    response = await zoom_data_source.list_accounts(page_size=5)
    print(f"Status: {response.status}")
    if response.status < 400:
        accounts_data = response.json()
        print(f"Accounts: {accounts_data}")
    else:
        print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Accounts → QSS (Quality Service Score)
    # ------------------------------------------------------------------
    print("\n=== Getting QSS report ===")
    response = await zoom_data_source.get_qss_report()
    print(f"Status: {response.status}")
    if response.status < 400:
        qss_data = response.json()
        print(f"QSS Report: {qss_data}")
    else:
        print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Accounts → SCIM 2
    # ------------------------------------------------------------------
    print("\n=== Listing SCIM users ===")
    response = await zoom_data_source.list_scim_users(page_size=10)
    print(f"Status: {response.status}")
    if response.status < 400:
        scim_users = response.json()
        print(f"SCIM Users: {scim_users}")
    else:
        print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Meetings (list for current user)
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing meetings for current user ===")
        response = await zoom_data_source.list_meetings(
            user_id=current_user_id,
            page_size=5,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            meetings_data = response.json()
            print(f"Meetings: {meetings_data}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Team Chat
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing team chat channels ===")
        response = await zoom_data_source.list_chat_channels(
            user_id=current_user_id,
            page_size=5,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            channels = response.json()
            print(f"Chat channels: {channels}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Phone
    # ------------------------------------------------------------------
    print("\n=== Listing phone users ===")
    response = await zoom_data_source.list_phone_users(page_size=5)
    print(f"Status: {response.status}")
    if response.status < 400:
        phone_users = response.json()
        print(f"Phone users: {phone_users}")
    else:
        print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Mail
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing mail messages ===")
        response = await zoom_data_source.list_mail_messages(
            user_id=current_user_id,
            page_size=5,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            mail_messages = response.json()
            print(f"Mail messages: {mail_messages}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Calendar
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing calendar events ===")
        response = await zoom_data_source.list_calendar_events(
            user_id=current_user_id,
            page_size=5,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            events = response.json()
            print(f"Calendar events: {events}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Scheduler
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing scheduler availability ===")
        response = await zoom_data_source.list_scheduler_availability(
            user_id=current_user_id,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            availability = response.json()
            print(f"Scheduler availability: {availability}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Rooms
    # ------------------------------------------------------------------
    print("\n=== Listing rooms ===")
    response = await zoom_data_source.list_rooms(page_size=5)
    print(f"Status: {response.status}")
    if response.status < 400:
        rooms = response.json()
        print(f"Rooms: {rooms}")
    else:
        print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Clips
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing clips ===")
        response = await zoom_data_source.list_clips(
            user_id=current_user_id,
            page_size=5,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            clips = response.json()
            print(f"Clips: {clips}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Whiteboard
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing whiteboards ===")
        response = await zoom_data_source.list_whiteboards(
            user_id=current_user_id,
            page_size=5,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            whiteboards = response.json()
            print(f"Whiteboards: {whiteboards}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → CRC (Call Recording)
    # ------------------------------------------------------------------
    print("\n=== Listing call recordings (CRC) ===")
    response = await zoom_data_source.list_call_recordings(page_size=5)
    print(f"Status: {response.status}")
    if response.status < 400:
        recordings = response.json()
        print(f"Call recordings: {recordings}")
    else:
        print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Chatbot
    # ------------------------------------------------------------------
    print("\n=== Listing chatbots ===")
    response = await zoom_data_source.list_chatbots(page_size=5)
    print(f"Status: {response.status}")
    if response.status < 400:
        chatbots = response.json()
        print(f"Chatbots: {chatbots}")
    else:
        print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → AI Companion
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Getting AI Companion insights ===")
        response = await zoom_data_source.get_ai_companion_insights(
            user_id=current_user_id,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            insights = response.json()
            print(f"AI Companion insights: {insights}")
        else:
            print(f"Error: {response.text}")

    # ------------------------------------------------------------------
    # Workplace → Zoom Docs
    # ------------------------------------------------------------------
    if current_user_id:
        print("\n=== Listing Zoom Docs documents ===")
        response = await zoom_data_source.list_documents(
            user_id=current_user_id,
            page_size=5,
        )
        print(f"Status: {response.status}")
        if response.status < 400:
            docs = response.json()
            print(f"Documents: {docs}")
        else:
            print(f"Error: {response.text}")


async def example_with_oauth() -> None:
    """Example using OAuth (Server-to-Server) authentication"""
    client_id = os.getenv("ZOOM_CLIENT_ID")
    client_secret = os.getenv("ZOOM_CLIENT_SECRET")
    account_id = os.getenv("ZOOM_ACCOUNT_ID")
    redirect_uri = os.getenv("ZOOM_REDIRECT_URI", "http://localhost:3001/connectors/oauth/callback/Zoom")

    if not client_id or not client_secret or not account_id:
        raise Exception(
            "ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, and ZOOM_ACCOUNT_ID must be set"
        )

    zoom_client: ZoomClient = ZoomClient.build_with_config(
        ZoomOAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            account_id=account_id,
            redirect_uri=redirect_uri,
        ),
    )
    print("Zoom client created with OAuth config")

    # Get access token via Server-to-Server OAuth
    client = zoom_client.get_client()
    if isinstance(client, ZoomRESTClientViaOAuth):
        access_token = await client.get_access_token_via_server_to_server()
        print(
            f"Access token obtained: {access_token[:20]}..."
            if access_token
            else "Failed to get token"
        )

    zoom_data_source = ZoomDataSource(zoom_client)
    print("Zoom data source created")
    print(zoom_data_source)

    # List users
    print("\n=== Listing users ===")
    response = await zoom_data_source.list_users(page_size=10)
    print(f"Status: {response.status}")
    if response.status < 400:
        users_data = response.json()
        print(f"Users: {users_data}")
    else:
        print(f"Error: {response.text}")

    # List meetings for the first user
    print("\n=== Listing meetings ===")
    users_response = await zoom_data_source.list_users(page_size=1)
    if users_response.status < 400:
        users_data = users_response.json()
        if users_data.get("users") and len(users_data["users"]) > 0:
            first_user_id = users_data["users"][0]["id"]
            meetings_response = await zoom_data_source.list_meetings(
                user_id=first_user_id, page_size=5
            )
            print(f"Status: {meetings_response.status}")
            if meetings_response.status < 400:
                meetings_data = meetings_response.json()
                print(f"Meetings: {meetings_data}")
            else:
                print(f"Error: {meetings_response.text}")


async def example_create_meeting() -> None:
    """Example creating a meeting"""
    token = os.getenv("ZOOM_ACCESS_TOKEN")
    if not token:
        raise Exception("ZOOM_ACCESS_TOKEN is not set")

    zoom_client: ZoomClient = ZoomClient.build_with_config(
        ZoomTokenConfig(token=token),
    )
    zoom_data_source = ZoomDataSource(zoom_client)

    # Get current user
    user_response = await zoom_data_source.get_user(user_id="me")
    if user_response.status >= 400:
        print(f"Error getting user: {user_response.text}")
        return

    user_data = user_response.json()
    user_id = user_data.get("id")

    # Create a meeting
    print("\n=== Creating a meeting ===")
    meeting_info = {
        "topic": "Test Meeting from API",
        "type": 2,  # Scheduled meeting
        "start_time": "2024-12-31T10:00:00Z",
        "duration": 30,
        "timezone": "UTC",
        "settings": {
            "host_video": True,
            "participant_video": True,
            "join_before_host": False,
            "mute_upon_entry": False,
        },
    }

    response = await zoom_data_source.create_meeting(
        user_id=user_id, meeting_info=meeting_info
    )
    print(f"Status: {response.status}")
    if response.status < 400:
        meeting_data = response.json()
        print(f"Meeting created: {meeting_data}")
        print(f"Join URL: {meeting_data.get('join_url')}")
    else:
        print(f"Error: {response.text}")


def main() -> None:
    """Main function to run examples"""
    print("Zoom API Examples")
    print("=" * 50)

    # Check which authentication method to use
    has_token = os.getenv("ZOOM_ACCESS_TOKEN")
    has_oauth = (
        os.getenv("ZOOM_CLIENT_ID")
        and os.getenv("ZOOM_CLIENT_SECRET")
        and os.getenv("ZOOM_ACCOUNT_ID")
    )

    if has_token:
        print("\n1. Running example with access token...")
        asyncio.run(example_with_token())
    elif has_oauth:
        print("\n1. Running example with OAuth...")
        asyncio.run(example_with_oauth())
    else:
        print(
            "\nError: Please set either ZOOM_ACCESS_TOKEN or "
            "(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_ACCOUNT_ID) environment variables"
        )
        return

    # Example: Create a meeting (requires token)
    if has_token:
        print("\n2. Running example to create a meeting...")
        asyncio.run(example_create_meeting())


if __name__ == "__main__":
    main()
