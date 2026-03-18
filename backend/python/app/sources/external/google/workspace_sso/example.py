# ruff: noqa
"""
Example script to demonstrate how to use the Google Workspace SSO API
"""
import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from app.sources.client.google.google import GoogleClient
from app.sources.external.google.workspace_sso.workspace_sso import GoogleWorkspaceSSODataSource

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
    admin_email = os.getenv("GOOGLE_ADMIN_EMAIL")
    if not admin_email:
        raise ValueError(
            "Either service_account_info must contain 'client_email', user_email must be provided, "
            "or GOOGLE_ADMIN_EMAIL must be set in environment"
        )

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

    workspace_sso_client = GoogleWorkspaceSSODataSource(enterprise_google_client.get_client())

    # List all domains
    print("Listing all domains...")
    try:
        results = await workspace_sso_client.domains_list(
            customer="my_customer"
        )
        print(f"Success! Found {len(results.get('domains', []))} domains")
        print(results)
    except Exception as e:
        print(f"Error listing domains: {e}")
        print(f"Error type: {type(e).__name__}")

    # List all roles
    print("\nListing all roles...")
    try:
        roles_results = await workspace_sso_client.roles_list(
            customer="my_customer"
        )
        print(f"Success! Found {len(roles_results.get('items', []))} roles")
        print(roles_results)
    except Exception as e:
        print(f"Error listing roles: {e}")
        print(f"Error type: {type(e).__name__}")

    # List all privileges
    print("\nListing all privileges...")
    try:
        privileges_results = await workspace_sso_client.privileges_list(
            customer="my_customer"
        )
        print(f"Success! Found {len(privileges_results.get('items', []))} privileges")
    except Exception as e:
        print(f"Error listing privileges: {e}")
        print(f"Error type: {type(e).__name__}")

    # Get customer info
    print("\nGetting customer info...")
    try:
        customer_info = await workspace_sso_client.customers_get(
            customerKey="my_customer"
        )
        print(f"Success! Customer: {customer_info.get('customerDomain', 'N/A')}")
        print(customer_info)
    except Exception as e:
        print(f"Error getting customer: {e}")
        print(f"Error type: {type(e).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
