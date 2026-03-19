# ruff: noqa

"""
DokuWiki API Usage Examples

This example demonstrates how to use the DokuWiki DataSource to interact with
the DokuWiki JSON API, covering:
- Authentication (Basic Auth, Bearer Token)
- Initializing the Client and DataSource
- Fetching Wiki Info
- Listing and Getting Pages
- Searching Pages
- Listing Extensions

Prerequisites:
For Basic Auth:
1. Set DOKUWIKI_INSTANCE_URL to your DokuWiki instance URL (e.g., wiki.example.com)
2. Set DOKUWIKI_USERNAME and DOKUWIKI_PASSWORD environment variables

For Bearer Token (JWT):
1. Set DOKUWIKI_INSTANCE_URL to your DokuWiki instance URL
2. Set DOKUWIKI_JWT_TOKEN environment variable

Note: DokuWiki does NOT use OAuth. Authentication is via HTTP Basic Auth
(username:password) or HTTP Bearer Token (JWT).
"""

import asyncio
import json
import os

from app.sources.client.dokuwiki.dokuwiki import (
    DokuWikiBasicAuthConfig,
    DokuWikiBearerTokenConfig,
    DokuWikiClient,
    DokuWikiResponse,
)
from app.sources.external.dokuwiki.dokuwiki import DokuWikiDataSource

# --- Configuration ---
# Instance URL (required for all auth types)
INSTANCE_URL = os.getenv("DOKUWIKI_INSTANCE_URL")

# Bearer Token credentials (highest priority)
JWT_TOKEN = os.getenv("DOKUWIKI_JWT_TOKEN")

# Basic Auth credentials (second priority)
USERNAME = os.getenv("DOKUWIKI_USERNAME")
PASSWORD = os.getenv("DOKUWIKI_PASSWORD")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: DokuWikiResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data is not None:
            data = response.data
            # Handle list responses
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2, default=str)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
            else:
                print(f"   Data: {data}")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing DokuWiki Client")

    if not INSTANCE_URL:
        print("  DOKUWIKI_INSTANCE_URL is required.")
        print("   Please set the environment variable to your DokuWiki instance URL.")
        return

    config = None

    # Priority 1: Bearer Token (JWT)
    if JWT_TOKEN:
        print("  Using Bearer Token (JWT) authentication")
        config = DokuWikiBearerTokenConfig(
            instance_url=INSTANCE_URL,
            jwt_token=JWT_TOKEN,
        )

    # Priority 2: Basic Auth
    if config is None and USERNAME and PASSWORD:
        print("  Using Basic Auth authentication")
        config = DokuWikiBasicAuthConfig(
            instance_url=INSTANCE_URL,
            username=USERNAME,
            password=PASSWORD,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - DOKUWIKI_JWT_TOKEN (for Bearer Token auth)")
        print("   - DOKUWIKI_USERNAME and DOKUWIKI_PASSWORD (for Basic Auth)")
        return

    client = DokuWikiClient.build_with_config(config)
    data_source = DokuWikiDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Who Am I
        print_section("Current User")
        whoami_resp = await data_source.who_am_i()
        print_result("Who Am I", whoami_resp)

        # 3. Wiki Version
        print_section("Wiki Info")
        version_resp = await data_source.get_wiki_version()
        print_result("Wiki Version", version_resp)

        # 4. Wiki Title
        title_resp = await data_source.get_wiki_title()
        print_result("Wiki Title", title_resp)

        # 5. List Pages
        print_section("Pages")
        pages_resp = await data_source.list_pages()
        print_result("List Pages", pages_resp)

        # Extract first page for further operations
        page_id = None
        if pages_resp.success and pages_resp.data:
            pages = pages_resp.data
            if isinstance(pages, list) and pages:
                first_page = pages[0]
                if isinstance(first_page, dict):
                    page_id = str(first_page.get("id", ""))
                    print(f"   Using Page: {page_id}")

        if page_id:
            # 6. Get Page Content
            print_section("Page Content")
            page_resp = await data_source.get_page(page=page_id)
            print_result("Get Page", page_resp)

        # 7. Search Pages
        print_section("Search")
        search_resp = await data_source.search_pages(query="wiki")
        print_result("Search Pages", search_resp)

        # 8. List Extensions
        print_section("Extensions")
        ext_resp = await data_source.list_extensions()
        print_result("List Extensions", ext_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All DokuWiki API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
