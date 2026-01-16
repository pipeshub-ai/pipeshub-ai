# ruff: noqa
"""
Example script to demonstrate how to use the Google Admin API
"""
import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient
from app.sources.external.google.admin.admin import GoogleAdminDataSource
from app.sources.external.google.drive.drive import GoogleDriveDataSource

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
    
    # List all users - customer parameter is REQUIRED
    print("Listing all users...")
    try:
        # Try with minimal parameters first
        results = await google_admin_client.users_list(
            customer="my_customer",
            maxResults=10  # Start with small number for testing
        )
        print(f"✅ Success! Found {len(results.get('users', []))} users")
        print("user results", results)
    except Exception as e:
        print(f"❌ Error listing users: {e}")
        print(f"Error type: {type(e).__name__}")
    
    
    # List all groups
    print("\nListing all groups...")
    try:
        groups_results = await google_admin_client.groups_list(
            customer="my_customer",
            maxResults=10  # Start with small number for testing
        )
        print(f"✅ Success! Found {len(groups_results.get('groups', []))} groups")
        print(groups_results)
        if groups_results.get('groups'):
            print(f"First group: {groups_results['groups'][0].get('email', 'N/A')}")
    except Exception as e:
        print(f"❌ Error listing groups: {e}")
        print(f"Error type: {type(e).__name__}")
    
    # List all drives
    print("\nListing all drives...")
    drives_results = await google_drive_client.drives_list(
        pageSize=10,  # Start with small number for testing
        # useDomainAdminAccess=True,
    )
    drives = drives_results.get('drives', [])
    print(f"✅ Success! Found {len(drives)} drives")
    print(drives_results)
        
    #     # List permissions for each drive
    #     if drives:
    #         for drive in drives[:3]:  # Limit to first 3 drives for testing
    #             drive_id = drive.get('id')
    #             drive_name = drive.get('name', 'N/A')
    #             print(f"\n📁 Listing permissions for drive: {drive_name} (ID: {drive_id})")
    #             try:
    #                 permissions_results = await google_drive_client.permissions_list(
    #                     fileId=drive_id,
    #                     pageSize=10,  # Start with small number for testing
    #                     supportsAllDrives=True,
    #                     useDomainAdminAccess=True,
    #                     fields="permissions(id, displayName, type, role, domain, emailAddress, deleted)"
    #                 )
    #                 permissions = permissions_results.get('permissions', [])
    #                 print(f"✅ Found {len(permissions)} permissions for drive '{drive_name}'")
    #                 print("\n\n permissions: ", permissions)
    #                 for perm in permissions:
    #                     perm_role = perm.get('role', 'N/A')
    #                     perm_type = perm.get('type', 'N/A')
    #                     perm_email = perm.get('emailAddress', 'N/A')
    #                     print(f"  - {perm_type}: {perm_email} ({perm_role})")
    #             except Exception as e:
    #                 print(f"❌ Error listing permissions for drive '{drive_name}': {e}")
    #                 print(f"Error type: {type(e).__name__}")
    # except Exception as e:
    #     print(f"❌ Error listing drives: {e}")
    #     print(f"Error type: {type(e).__name__}")

    # permissions_results = await google_drive_client.permissions_list(
    #                     fileId="1r4nhP3f4d29-xhRePaq1tfY7FvCpneuU",
    #                     supportsAllDrives=True,
    #                     # useDomainAdminAccess=True,
    #                     fields="permissions(id, displayName, type, role, domain, emailAddress, deleted)"
    #                 )
    # print("\n\n\n permissions: ", permissions_results)

if __name__ == "__main__":
    asyncio.run(main())
