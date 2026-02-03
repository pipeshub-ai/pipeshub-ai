# ruff: noqa
"""
Example script to demonstrate how to use the Google Drive API
"""
import asyncio
import logging
from typing import Optional, Dict, Any
import os

from app.sources.client.google.google import GoogleClient
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.drive.drive import GoogleDriveDataSource

try:
    from google.oauth2 import service_account  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
except ImportError:
    print("Google API client libraries not found. Please install them using 'pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib'")
    raise


async def build_individual_client_from_credentials(
    service_name: str = "drive",
    access_token: str = "",
    refresh_token: str = "",
    client_id: str = "",
    client_secret: str = "",
    scopes: Optional[list] = None,
    version: str = "v3",
) -> GoogleClient:
    """
    Build GoogleClient for individual account using credentials directly.
    
    Args:
        service_name: Name of the Google service (e.g., "drive", "gmail")
        access_token: OAuth2 access token
        refresh_token: OAuth2 refresh token
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret
        scopes: Optional list of scopes (uses defaults if not provided)
        version: API version (default: "v3")
    
    Returns:
        GoogleClient instance
    """
    # Get optimized scopes for the service
    optimized_scopes = GoogleClient._get_optimized_scopes(service_name, scopes)
    
    # Create credentials object
    google_credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=optimized_scopes,
    )
    
    # Create Google service client
    client = build(service_name, version, credentials=google_credentials)
    
    return GoogleClient(client)


async def build_enterprise_client_from_credentials(
    service_name: str = "drive",
    service_account_info: Dict[str, Any] = None,
    user_email: Optional[str] = None,
    scopes: Optional[list] = None,
    version: str = "v3",
) -> GoogleClient:
    """
    Build GoogleClient for enterprise account using service account credentials directly.
    
    Args:
        service_name: Name of the Google service (e.g., "drive", "gmail")
        service_account_info: Service account JSON key as a dictionary
        user_email: Optional user email for impersonation (defaults to admin email from service_account_info)
        scopes: Optional list of scopes (uses defaults if not provided)
        version: API version (default: "v3")
    
    Returns:
        GoogleClient instance
    """
    if service_account_info is None:
        raise ValueError("service_account_info is required for enterprise accounts")
    
    # Get optimized scopes for the service
    optimized_scopes = GoogleClient._get_optimized_scopes(service_name, scopes)
    
    # Get admin email from service account info or use provided user_email
    admin_email = service_account_info.get("client_email") or user_email
    if not admin_email:
        raise ValueError("Either service_account_info must contain 'client_email' or user_email must be provided")
    
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
    # ============================================
    # Option 1: Using credentials directly (Individual Account)
    # ============================================
    # Uncomment and fill in your credentials:
    individual_google_client = await build_individual_client_from_credentials(
        service_name="drive",
        access_token= os.getenv("GOOGLE_ACCESS_TOKEN"),
        refresh_token= os.getenv("GOOGLE_REFRESH_TOKEN"),
        client_id= os.getenv("GOOGLE_CLIENT_ID"),
        client_secret= os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],  # Optional
    )
    
    google_drive_client = GoogleDriveDataSource(individual_google_client.get_client())
    print("Listing files (Individual Account)")
    results = await google_drive_client.files_list()
    print(results)
    print("length of results", len(results.get("files", [])))

    # Fetch app user
    print("Fetching app user")
    fields = 'user(displayName,emailAddress,permissionId)'
    user_about = await google_drive_client.about_get(fields=fields)
    print(user_about)

    #fetch drive info
    print("Fetching drive info")
    drive_info = await google_drive_client.files_get(fileId="root")
    print(drive_info)

    # Fetch users drives 
    # print("Fetching users drives")
    # drives = await google_drive_client.drives_list()
    # print(drives)
    
    

if __name__ == "__main__":
    asyncio.run(main())
