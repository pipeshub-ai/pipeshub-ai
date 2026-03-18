# ruff: noqa

"""
DokuWiki XML-RPC API Usage Examples

This example demonstrates how to use the DokuWiki DataSource to interact with
the DokuWiki XML-RPC API, covering:
- Authentication (Basic Auth via XML-RPC transport)
- Initializing the Client and DataSource
- Getting version and server time
- Page operations (get, put, list, info, versions)
- Search
- Attachments and backlinks
- Recent changes
- ACL checks

Prerequisites:
1. Set DOKUWIKI_INSTANCE_URL to your DokuWiki instance (e.g. "wiki.example.com")
2. Set DOKUWIKI_USERNAME and DOKUWIKI_PASSWORD
3. Ensure XML-RPC is enabled in DokuWiki configuration
   (Configuration Manager > Authentication > Remote Access)
"""

import json
import os

from app.sources.client.dokuwiki.dokuwiki import (
    DokuWikiBasicAuthConfig,
    DokuWikiClient,
    DokuWikiResponse,
)
from app.sources.external.dokuwiki.dokuwiki import DokuWikiDataSource

# --- Configuration ---
INSTANCE_URL = os.getenv("DOKUWIKI_INSTANCE_URL", "")
USERNAME = os.getenv("DOKUWIKI_USERNAME", "")
PASSWORD = os.getenv("DOKUWIKI_PASSWORD", "")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: DokuWikiResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data is not None:
            data = response.data
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    item = data[0]
                    if isinstance(item, dict):
                        print(f"   Sample: {json.dumps(item, indent=2, default=str)[:400]}...")
                    else:
                        print(f"   Sample: {str(item)[:400]}...")
            elif isinstance(data, dict):
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
            elif isinstance(data, str):
                print(f"   Content: {data[:400]}...")
            else:
                print(f"   Value: {data}")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


def main() -> None:
    # 1. Initialize Client
    print_section("Initializing DokuWiki Client")

    if not INSTANCE_URL or not USERNAME or not PASSWORD:
        print("  No valid authentication found.")
        print("   Please set:")
        print("   - DOKUWIKI_INSTANCE_URL (e.g. wiki.example.com)")
        print("   - DOKUWIKI_USERNAME")
        print("   - DOKUWIKI_PASSWORD")
        return

    config = DokuWikiBasicAuthConfig(
        instance_url=INSTANCE_URL,
        username=USERNAME,
        password=PASSWORD,
    )
    client = DokuWikiClient.build_with_config(config)
    data_source = DokuWikiDataSource(client)
    print(f"Client initialized for instance: {INSTANCE_URL}")

    # 2. Get Version
    print_section("DokuWiki Version")
    version_resp = data_source.get_version()
    print_result("Get Version", version_resp)

    # 3. Get Server Time
    print_section("Server Time")
    time_resp = data_source.get_time()
    print_result("Get Time", time_resp)

    # 4. Get All Pages
    print_section("All Pages")
    all_pages_resp = data_source.get_all_pages()
    print_result("Get All Pages", all_pages_resp)

    # Get first page name for further operations
    pagename = None
    if all_pages_resp.success and isinstance(all_pages_resp.data, list) and all_pages_resp.data:
        first_page = all_pages_resp.data[0]
        if isinstance(first_page, dict):
            pagename = str(first_page.get("id", ""))
        print(f"   Using page: {pagename}")

    # 5. Get Page Content
    if pagename:
        print_section(f"Page Content: {pagename}")
        page_resp = data_source.get_page(pagename)
        print_result("Get Page", page_resp)

        # 6. Get Page Info
        print_section(f"Page Info: {pagename}")
        info_resp = data_source.get_page_info(pagename)
        print_result("Get Page Info", info_resp)

        # 7. Get Page Versions
        print_section(f"Page Versions: {pagename}")
        versions_resp = data_source.get_page_versions(pagename)
        print_result("Get Page Versions", versions_resp)

        # 8. Get Backlinks
        print_section(f"Backlinks: {pagename}")
        backlinks_resp = data_source.get_backlinks(pagename)
        print_result("Get Backlinks", backlinks_resp)

        # 9. ACL Check
        print_section(f"ACL Check: {pagename}")
        acl_resp = data_source.acl_check(pagename)
        print_result("ACL Check", acl_resp)

    # 10. Search
    print_section("Search")
    search_resp = data_source.search("wiki")
    print_result("Search 'wiki'", search_resp)

    # 11. List Pages in namespace
    print_section("List Pages in Root Namespace")
    list_resp = data_source.list_pages("")
    print_result("List Pages", list_resp)

    # 12. Get Recent Changes (last 24 hours)
    import time
    print_section("Recent Changes (last 24h)")
    yesterday = int(time.time()) - 86400
    changes_resp = data_source.get_recent_changes(yesterday)
    print_result("Get Recent Changes", changes_resp)

    # 13. Get Attachments in root namespace
    print_section("Attachments")
    attachments_resp = data_source.get_attachments("")
    print_result("Get Attachments", attachments_resp)

    print("\n" + "=" * 80)
    print("  All DokuWiki API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    main()
