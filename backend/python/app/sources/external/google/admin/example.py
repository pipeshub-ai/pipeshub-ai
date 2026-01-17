# ruff: noqa
"""
Example script to demonstrate how to use the Google Admin API
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient
from app.sources.external.google.admin.admin import GoogleAdminDataSource
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource

try:
    from google.oauth2 import service_account  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
except ImportError:
    print("Google API client libraries not found. Please install them using 'pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib'")
    raise


async def build_enterprise_client_from_credentials(
    service_name: str = "admin",
    service_account_info: Optional[Dict[str, Any]] = None,
    service_account_file: Optional[str] = None,
    user_email: Optional[str] = None,
    scopes: Optional[list] = None,
    version: str = "directory_v1",
) -> GoogleClient:
    """
    Build GoogleClient for enterprise account using service account credentials from .env.
    
    Args:
        service_name: Name of the Google service (e.g., "admin", "drive")
        service_account_info: Service account JSON key as a dictionary (optional)
        service_account_file: Path to service account JSON file (optional, from GOOGLE_SERVICE_ACCOUNT_FILE)
        user_email: Optional user email for impersonation (from GOOGLE_ADMIN_EMAIL or service account client_email)
        scopes: Optional list of scopes (uses defaults if not provided)
        version: API version (default: "directory_v1" for admin)
    
    Returns:
        GoogleClient instance
    """
    # Load service account info from file if provided
    if service_account_file:
        with open(service_account_file, 'r') as f:
            service_account_info = json.load(f)
    elif not service_account_info:
        # Try to load from environment variable as JSON string
        service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if service_account_json:
            service_account_info = json.loads(service_account_json)
        else:
            # Try to load from file path in env
            service_account_file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
            if service_account_file_path:
                with open(service_account_file_path, 'r') as f:
                    service_account_info = json.load(f)
            else:
                raise ValueError(
                    "service_account_info, service_account_file, GOOGLE_SERVICE_ACCOUNT_JSON, "
                    "or GOOGLE_SERVICE_ACCOUNT_FILE must be provided"
                )
    
    # Get optimized scopes for the service
    optimized_scopes = GoogleClient._get_optimized_scopes(service_name, scopes)
    
    # Get admin email from service account info or use provided user_email
    admin_email =os.getenv("GOOGLE_ADMIN_EMAIL")
    if not admin_email:
        raise ValueError(
            "Either service_account_info must contain 'client_email', user_email must be provided, "
            "or GOOGLE_ADMIN_EMAIL must be set in environment"
        )

    # print(f"Service account info: {service_account_info}")
    # print(f"Admin email: {admin_email}")
    
    # Create service account credentials
    google_credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=optimized_scopes,
        subject=(user_email or admin_email),
    )
    
    # Create Google service client
    client = build(
        service_name,
        version,
        credentials=google_credentials,
        cache_discovery=False,
    )
    
    return GoogleClient(client)


async def main() -> None:
    # Build enterprise client from .env credentials
    # Supports:
    # - GOOGLE_SERVICE_ACCOUNT_FILE: Path to service account JSON file
    # - GOOGLE_SERVICE_ACCOUNT_JSON: Service account JSON as string
    # - GOOGLE_ADMIN_EMAIL: Admin email for impersonation (optional, uses client_email if not provided)
    
    enterprise_google_client = await build_enterprise_client_from_credentials(
        service_name="admin",
        version="directory_v1",
        service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"),
        user_email=os.getenv("GOOGLE_ADMIN_EMAIL"),
    )

    google_admin_client = GoogleAdminDataSource(enterprise_google_client.get_client())
    
    # Build Drive client for listing drives and permissions
    enterprise_drive_client = await build_enterprise_client_from_credentials(
        service_name="drive",
        version="v3",
        service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"),
        # user_email=os.getenv("GOOGLE_ADMIN_EMAIL"),
        user_email="vishwjeet.pawar@pipeshub.app",
    )
    
    google_drive_client = GoogleDriveDataSource(enterprise_drive_client.get_client())
    
    # # List all users - customer parameter is REQUIRED
    # print("Listing all users...")
    # try:
    #     # Try with minimal parameters first
    #     results = await google_admin_client.users_list(
    #         customer="my_customer",
    #         maxResults=10  # Start with small number for testing
    #     )
    #     print(f"✅ Success! Found {len(results.get('users', []))} users")
    #     print("user results", results)
    # except Exception as e:
    #     print(f"❌ Error listing users: {e}")
    #     print(f"Error type: {type(e).__name__}")
    
    
    # # List all groups
    # print("\nListing all groups...")
    # try:
    #     groups_results = await google_admin_client.groups_list(
    #         customer="my_customer",
    #         maxResults=10  # Start with small number for testing
    #     )
    #     print(f"✅ Success! Found {len(groups_results.get('groups', []))} groups")
    #     print(groups_results)
    #     if groups_results.get('groups'):
    #         print(f"First group: {groups_results['groups'][0].get('email', 'N/A')}")
    # except Exception as e:
    #     print(f"❌ Error listing groups: {e}")
    #     print(f"Error type: {type(e).__name__}")
    
    # # Build Gmail client for listing labels and messages
    # print("\n" + "="*60)
    # print("Gmail Labels and Messages")
    # print("="*60)
    
    try:
        # Get user email for Gmail API (use the same user email as Drive)
        user_email = "abhishek@pipeshub.app"
        
        enterprise_gmail_client = await build_enterprise_client_from_credentials(
            service_name="gmail",
            version="v1",
            service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"),
            user_email=user_email,
        )
        
        gmail_data_source = GoogleGmailDataSource(enterprise_gmail_client.get_client())
        
        # List all labels
        print(f"\n📋 Listing all labels for user: {user_email}...")
        labels_response = await gmail_data_source.users_labels_list(userId=user_email)
        print("labels_response", labels_response)
        labels = labels_response.get("labels", [])
        
        if labels:
            print(f"✅ Found {len(labels)} labels:")
            for label in labels:
                label_id = label.get("id", "")
                label_name = label.get("name", "")
                label_type = label.get("type", "")
                print(f"  - {label_name} (ID: {label_id}, Type: {label_type})")
            
            # Get 3 messages from each label
            # print(f"\n📧 Fetching 3 messages from each label...")
            # total_messages_found = 0
            
            # for label in labels:
            #     label_id = label.get("id", "")
            #     label_name = label.get("name", "")
                
            #     try:
            #         # Try to get messages with this label
            #         # Note: labelIds should be a list according to Gmail API
            #         messages_response = await gmail_data_source.users_messages_list(
            #             userId=user_email,
            #             labelIds=[label_id],  # Pass as list
            #             maxResults=3
            #         )
            #         messages = messages_response.get("messages", [])
                    
            #         if messages:
            #             print(f"\n  📁 Label: {label_name} ({label_id})")
            #             print(f"     Found {len(messages)} message(s):")
            #             for i, msg in enumerate(messages, 1):
            #                 msg_id = msg.get("id", "N/A")
            #                 print(f"       {i}. Message ID: {msg_id}")
            #             total_messages_found += len(messages)
            #         else:
            #             print(f"  📁 Label: {label_name} ({label_id}) - No messages found")
                        
            #     except Exception as e:
            #         print(f"  ⚠️  Error fetching messages for label '{label_name}': {e}")
            #         continue
            
            # if total_messages_found == 0:
            #     print("\n⚠️  No messages found in any label. Fetching last 5 messages instead...")
            #     messages_response = await gmail_data_source.users_messages_list(
            #         userId=user_email,
            #         maxResults=5
            #     )
            #     messages = messages_response.get("messages", [])
            #     print(f"\n✅ Found {len(messages)} messages:")
            #     for i, msg in enumerate(messages, 1):
            #         print(f"  {i}. Message ID: {msg.get('id', 'N/A')}")
            # else:
            #     print(f"\n✅ Total messages found across all labels: {total_messages_found}")

        # Fetch threads first, then all messages in each thread
        print("\n📧 Fetching threads...")
        threads_response = await gmail_data_source.users_threads_list(
            userId=user_email,
            maxResults=5
        )
        threads = threads_response.get("threads", [])
        print(f"\n✅ Found {len(threads)} threads. Fetching all messages for each thread...\n")
        
        # Helper function to extract headers from message payload
        def extract_headers(payload):
            headers = {}
            if isinstance(payload, dict):
                for header in payload.get("headers", []):
                    name = header.get("name", "").lower()
                    value = header.get("value", "")
                    headers[name] = value
            return headers
        
        # Helper function to format date
        def format_date(internal_date):
            if not internal_date:
                return "N/A"
            try:
                timestamp = int(internal_date) / 1000
                return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            except:
                return str(internal_date)
        
        # Process each thread
        for thread_num, thread in enumerate(threads, 1):
            thread_id = thread.get("id", "N/A")
            if thread_id == "N/A":
                continue
            
            try:
                # Get full thread details (includes all messages in the thread)
                thread_details = await gmail_data_source.users_threads_get(
                    userId=user_email,
                    id=thread_id,
                    format="full"
                )
                
                messages = thread_details.get("messages", [])
                history_id = thread_details.get("historyId", "N/A")
                
                print("=" * 80)
                print(f"🧵 Thread {thread_num} of {len(threads)}")
                print("=" * 80)
                print(f"Thread ID: {thread_id}")
                print(f"History ID: {history_id}")
                print(f"Number of messages in thread: {len(messages)}")
                print()
                
                # Display each message in the thread
                for msg_num, message in enumerate(messages, 1):
                    msg_id = message.get("id", "N/A")
                    if msg_id == "N/A":
                        continue
                    
                    # Extract headers
                    payload = message.get("payload", {})
                    headers = extract_headers(payload)
                    
                    # Extract other message info
                    snippet = message.get("snippet", "No preview available")
                    label_ids = message.get("labelIds", [])
                    internal_date = message.get("internalDate", "")
                    date_str = format_date(internal_date)
                    
                    # Print message details
                    print("-" * 80)
                    print(f"  📨 Message {msg_num} of {len(messages)} in Thread")
                    print("-" * 80)
                    print(f"  Message ID: {msg_id}")
                    print(f"  Date: {date_str}")
                    print(f"  From: {headers.get('from', 'N/A')}")
                    print(f"  To: {headers.get('to', 'N/A')}")
                    if headers.get('cc'):
                        print(f"  CC: {headers.get('cc', 'N/A')}")
                    if headers.get('bcc'):
                        print(f"  BCC: {headers.get('bcc', 'N/A')}")
                    print(f"  Subject: {headers.get('subject', 'No Subject')}")
                    print(f"  Labels: {', '.join(label_ids) if label_ids else 'None'}")
                    print(f"  Preview: {snippet}")
                    print()
                
                print()  # Extra line between threads
                
            except Exception as e:
                print(f"  ⚠️  Error fetching thread {thread_id}: {e}")
                import traceback
                traceback.print_exc()
                print()


        # #----------------------! Print last 25 messages to check sort order !-----------------------------------#
        # print("\n📧 Fetching last 25 messages to check sort order...")
        # messages_response = await gmail_data_source.users_messages_list(
        #     userId=user_email,
        #     maxResults=10
        # )
        # print("messages_response", messages_response)
        # print("\n\n\n\n\n")
        # messages = messages_response.get("messages", [])
        # print(f"\n✅ Found {len(messages)} messages. Fetching details...\n")
        
        # # Helper function to format date
        # def format_date(internal_date):
        #     if not internal_date:
        #         return "N/A"
        #     try:
        #         timestamp = int(internal_date) / 1000
        #         return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        #     except:
        #         return str(internal_date)
        
        # # Fetch and display full details for each message
        # for i, msg in enumerate(messages, 1):
        #     msg_id = msg.get("id", "N/A")
        #     if msg_id == "N/A":
        #         continue
                
        #     try:
        #         # Get full message details
        #         message_details = await gmail_data_source.users_messages_get(
        #             userId=user_email,
        #             id=msg_id,
        #             format="full"
        #         )
        #         print("message_details", message_details)
                
        #         # Extract headers
        #         headers = {}
        #         for header in message_details.get("payload", {}).get("headers", []):
        #             name = header.get("name", "").lower()
        #             value = header.get("value", "")
        #             headers[name] = value
                
        #         # Extract other message info
        #         snippet = message_details.get("snippet", "No preview available")
        #         thread_id = message_details.get("threadId", "N/A")
        #         label_ids = message_details.get("labelIds", [])
        #         internal_date = message_details.get("internalDate", "")
                
        #         # Format date
        #         date_str = format_date(internal_date)
                
        #         # Print message details with emphasis on date for sort order checking
        #         print("=" * 80)
        #         print(f"📨 Message {i} of {len(messages)}")
        #         print("=" * 80)
        #         print(f"Date: {date_str} (Internal Date: {internal_date})")
        #         print(f"Message ID: {msg_id}")
        #         print(f"Thread ID: {thread_id}")
        #         print(f"From: {headers.get('from', 'N/A')}")
        #         print(f"To: {headers.get('to', 'N/A')}")
        #         if headers.get('cc'):
        #             print(f"CC: {headers.get('cc', 'N/A')}")
        #         if headers.get('bcc'):
        #             print(f"BCC: {headers.get('bcc', 'N/A')}")
        #         print(f"Subject: {headers.get('subject', 'No Subject')}")
        #         print(f"Labels: {', '.join(label_ids) if label_ids else 'None'}")
        #         print(f"\nPreview:")
        #         print(f"  {snippet}")
        #         print()
                
        #     except Exception as e:
        #         print(f"  ⚠️  Error fetching details for message {msg_id}: {e}")
        #         print()
        
        # # Summary of sort order
        # print("\n" + "=" * 80)
        # print("SORT ORDER SUMMARY")
        # print("=" * 80)
        # print("Messages are displayed in the order returned by the API.")
        # print("Check the dates above to verify if they are sorted (newest first or oldest first).")
        # print("=" * 80)
                
    except Exception as e:
        print(f"❌ Error with Gmail API: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
    

if __name__ == "__main__":
    asyncio.run(main())
