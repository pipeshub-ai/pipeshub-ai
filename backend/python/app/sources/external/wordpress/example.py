# ruff: noqa

"""
WordPress API Usage Examples

This example demonstrates how to use the WordPress DataSource to interact with
the WordPress REST API, covering:
- Authentication (OAuth2 for WordPress.com, Application Password for self-hosted, Token)
- Initializing the Client and DataSource
- Getting Current User
- Listing Posts and Pages
- Listing Categories
- Searching Content

Prerequisites:
For OAuth2 (WordPress.com):
1. Create a WordPress.com OAuth app at https://developer.wordpress.com/apps/
2. Set WORDPRESS_CLIENT_ID and WORDPRESS_CLIENT_SECRET environment variables
3. Set WORDPRESS_SITE_ID to your WordPress.com site ID or domain
4. The OAuth flow will automatically open a browser for authorization

For Application Password (self-hosted):
1. Log in to your self-hosted WordPress admin
2. Go to Users > Profile > Application Passwords
3. Generate a new application password
4. Set WORDPRESS_SITE_URL, WORDPRESS_USERNAME, and WORDPRESS_APP_PASSWORD

For Bearer Token:
1. Set WORDPRESS_ACCESS_TOKEN and WORDPRESS_SITE_URL environment variables
"""

import asyncio
import json
import os

from app.sources.client.wordpress.wordpress import (
    WordPressClient,
    WordPressOAuthConfig,
    WordPressApplicationPasswordConfig,
    WordPressTokenConfig,
    WordPressResponse,
)
from app.sources.external.wordpress.wordpress import WordPressDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority - WordPress.com)
CLIENT_ID = os.getenv("WORDPRESS_CLIENT_ID")
CLIENT_SECRET = os.getenv("WORDPRESS_CLIENT_SECRET")
SITE_ID = os.getenv("WORDPRESS_SITE_ID")

# Application Password (second priority - self-hosted)
SITE_URL = os.getenv("WORDPRESS_SITE_URL")
USERNAME = os.getenv("WORDPRESS_USERNAME")
APP_PASSWORD = os.getenv("WORDPRESS_APP_PASSWORD")

# Bearer Token (third priority)
ACCESS_TOKEN = os.getenv("WORDPRESS_ACCESS_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("WORDPRESS_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: WordPressResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            if isinstance(data, list):
                print(f"   Found {len(data)} items.")
                if data:
                    print(f"   Sample: {json.dumps(data[0], indent=2)[:400]}...")
                return
            # Handle dict responses with common WordPress keys
            for key in ("posts", "pages", "categories", "tags", "comments",
                        "users", "media", "results"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing WordPress Client")

    config = None

    # Priority 1: OAuth2 (WordPress.com)
    if CLIENT_ID and CLIENT_SECRET and SITE_ID:
        print("  Using OAuth2 authentication (WordPress.com)")
        try:
            print("Starting OAuth flow...")
            # WordPress.com OAuth authorization URL
            # Token endpoint: https://public-api.wordpress.com/oauth2/token
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://public-api.wordpress.com/oauth2/authorize",
                token_endpoint="https://public-api.wordpress.com/oauth2/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],  # WordPress.com scopes are configured in the app settings
                scope_delimiter=" ",
                auth_method="body",  # WordPress.com sends credentials in POST body
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = WordPressOAuthConfig(
                access_token=access_token,
                site_id=SITE_ID,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Application Password (self-hosted)
    if config is None and SITE_URL and USERNAME and APP_PASSWORD:
        print("  Using Application Password authentication (self-hosted)")
        config = WordPressApplicationPasswordConfig(
            site_url=SITE_URL,
            username=USERNAME,
            application_password=APP_PASSWORD,
        )

    # Priority 3: Bearer Token
    if config is None and ACCESS_TOKEN and SITE_URL:
        print("  Using Bearer Token authentication")
        config = WordPressTokenConfig(
            token=ACCESS_TOKEN,
            site_url=SITE_URL,
        )

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - WORDPRESS_CLIENT_ID, WORDPRESS_CLIENT_SECRET, and WORDPRESS_SITE_ID (for OAuth2 / WordPress.com)")
        print("   - WORDPRESS_SITE_URL, WORDPRESS_USERNAME, and WORDPRESS_APP_PASSWORD (for Application Password)")
        print("   - WORDPRESS_ACCESS_TOKEN and WORDPRESS_SITE_URL (for Bearer Token)")
        return

    client = WordPressClient.build_with_config(config)
    data_source = WordPressDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Posts
        print_section("Posts")
        posts_resp = await data_source.list_posts(per_page=5)
        print_result("List Posts", posts_resp)

        # 4. List Pages
        print_section("Pages")
        pages_resp = await data_source.list_pages(per_page=5)
        print_result("List Pages", pages_resp)

        # 5. List Categories
        print_section("Categories")
        categories_resp = await data_source.list_categories(per_page=10)
        print_result("List Categories", categories_resp)

        # 6. List Tags
        print_section("Tags")
        tags_resp = await data_source.list_tags(per_page=10)
        print_result("List Tags", tags_resp)

        # 7. Search Content
        print_section("Search")
        search_resp = await data_source.search_content(search="hello")
        print_result("Search Content", search_resp)

        # 8. Get a specific post if available
        if posts_resp.success and posts_resp.data:
            data = posts_resp.data
            posts = data if isinstance(data, list) else []
            if posts:
                post_id = str(posts[0].get("id", ""))
                if post_id:
                    print_section(f"Post Details: {posts[0].get('title', {}).get('rendered', 'N/A')}")
                    post_resp = await data_source.get_post(post_id=post_id)
                    print_result("Get Post", post_resp)

                    # 9. Get Comments for the post
                    print_section("Post Comments")
                    comments_resp = await data_source.list_comments(post=int(post_id))
                    print_result("List Comments", comments_resp)

        # 10. List Users
        print_section("Users")
        users_resp = await data_source.list_users(per_page=5)
        print_result("List Users", users_resp)

        # 11. List Post Types
        print_section("Post Types")
        types_resp = await data_source.list_post_types()
        print_result("List Post Types", types_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All WordPress API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
