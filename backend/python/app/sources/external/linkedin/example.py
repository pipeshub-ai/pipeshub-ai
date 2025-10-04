"""
LinkedIn Data Source Example

This example demonstrates how to use the LinkedIn data source to interact with LinkedIn's REST API.
It shows various operations including:
- Retrieving profile information
- Managing posts and shares
- Getting analytics and statistics
- Working with connections

Prerequisites:
- LinkedIn OAuth 2.0 access token with appropriate scopes
- Set LINKEDIN_ACCESS_TOKEN environment variable

Example usage:
    export LINKEDIN_ACCESS_TOKEN="your_access_token_here"
    python example.py
"""

import asyncio
import os

from app.sources.client.linkedin.linkedin import LinkedInClient, LinkedInOAuth2Config
from app.sources.external.linkedin.linkedin import LinkedInDataSource


async def test_profile_operations(linkedin: LinkedInDataSource) -> None:
    """Test profile-related operations"""
    print("\n" + "="*60)
    print("TESTING PROFILE OPERATIONS")
    print("="*60)

    # Get current user's profile
    print("\n1. Getting current user's profile...")
    response = await linkedin.get_profile()
    if response.success:
        print("✅ Profile retrieved successfully")
        print(f"   Data: {response.data}")
    else:
        print(f"❌ Error: {response.error}")


async def test_organization_operations(linkedin: LinkedInDataSource) -> None:
    """Test organization-related operations"""
    print("\n" + "="*60)
    print("TESTING ORGANIZATION OPERATIONS")
    print("="*60)

    # Note: Replace with actual organization ID
    org_id = "12345678"

    print(f"\n1. Getting organization details (ID: {org_id})...")
    response = await linkedin.get_organization(id=org_id)
    if response.success:
        print("✅ Organization retrieved successfully")
        print(f"   Data: {response.data}")
    else:
        print(f"❌ Error: {response.error}")


async def test_share_operations(linkedin: LinkedInDataSource) -> None:
    """Test share/post-related operations"""
    print("\n" + "="*60)
    print("TESTING SHARE/POST OPERATIONS")
    print("="*60)

    print("\n1. Getting user's shares...")
    response = await linkedin.get_shares(
        q="authors",
        count=10
    )
    if response.success:
        print("✅ Shares retrieved successfully")
        print(f"   Data: {response.data}")
    else:
        print(f"❌ Error: {response.error}")

    # Example: Create a share (commented out to avoid accidental posting)
    # print("\n2. Creating a new share...")
    # response = await linkedin.create_share(
    #     author="urn:li:person:YOUR_PERSON_URN",
    #     specificContent={
    #         "com.linkedin.ugc.ShareContent": {
    #             "shareCommentary": {
    #                 "text": "Test post from LinkedIn Data Source"
    #             },
    #             "shareMediaCategory": "NONE"
    #         }
    #     },
    #     visibility={
    #         "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
    #     }
    # )
    # if response.success:
    #     print(f"✅ Share created successfully")
    #     print(f"   Data: {response.data}")
    # else:
    #     print(f"❌ Error: {response.error}")


async def test_analytics_operations(linkedin: LinkedInDataSource) -> None:
    """Test analytics-related operations"""
    print("\n" + "="*60)
    print("TESTING ANALYTICS OPERATIONS")
    print("="*60)

    # Note: Replace with actual organization URN
    org_urn = "urn:li:organization:12345678"

    print("\n1. Getting follower statistics...")
    response = await linkedin.get_organization_follower_statistics(
        q="organizationalEntity",
        organizationalEntity=org_urn
    )
    if response.success:
        print("✅ Follower statistics retrieved successfully")
        print(f"   Data: {response.data}")
    else:
        print(f"❌ Error: {response.error}")


async def test_connection_operations(linkedin: LinkedInDataSource) -> None:
    """Test connection-related operations"""
    print("\n" + "="*60)
    print("TESTING CONNECTION OPERATIONS")
    print("="*60)

    print("\n1. Getting connections...")
    response = await linkedin.get_connections(count=10)
    if response.success:
        print("✅ Connections retrieved successfully")
        print(f"   Data: {response.data}")
    else:
        print(f"❌ Error: {response.error}")


async def test_search_operations(linkedin: LinkedInDataSource) -> None:
    """Test search-related operations"""
    print("\n" + "="*60)
    print("TESTING SEARCH OPERATIONS")
    print("="*60)

    print("\n1. Searching companies...")
    response = await linkedin.search_companies(
        q="technology",
        count=5
    )
    if response.success:
        print("✅ Companies search completed successfully")
        print(f"   Data: {response.data}")
    else:
        print(f"❌ Error: {response.error}")


async def main() -> None:
    """Main function to run all tests"""
    # Get access token from environment
    access_token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if not access_token:
        print("❌ Error: LINKEDIN_ACCESS_TOKEN environment variable not set")
        print("\nTo use this example:")
        print("1. Get a LinkedIn OAuth 2.0 access token")
        print("2. Set the environment variable:")
        print("   export LINKEDIN_ACCESS_TOKEN='your_access_token_here'")
        print("3. Run this script again")
        return

    print("="*60)
    print("LINKEDIN DATA SOURCE EXAMPLE")
    print("="*60)
    print(f"\nAccess token: {access_token[:20]}..." if len(access_token) > 20 else access_token)

    # Create LinkedIn client
    linkedin_client = LinkedInClient.build_with_config(
        LinkedInOAuth2Config(access_token=access_token)
    )
    print("✅ LinkedIn client created successfully")

    # Create LinkedIn data source
    linkedin_data_source = LinkedInDataSource(linkedin_client)
    print("✅ LinkedIn data source created successfully")

    try:
        # Run all test operations
        await test_profile_operations(linkedin_data_source)
        await test_organization_operations(linkedin_data_source)
        await test_share_operations(linkedin_data_source)
        await test_analytics_operations(linkedin_data_source)
        await test_connection_operations(linkedin_data_source)
        await test_search_operations(linkedin_data_source)

        print("\n" + "="*60)
        print("ALL TESTS COMPLETED")
        print("="*60)

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Cleanup
        await linkedin_client.close()
        print("\n✅ LinkedIn client closed")


if __name__ == "__main__":
    asyncio.run(main())
