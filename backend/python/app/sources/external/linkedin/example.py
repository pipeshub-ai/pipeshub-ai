"""
LinkedIn Data Source Example - Official SDK Integration

This example demonstrates how to use the LinkedIn data source with the official
linkedin-api-client SDK. It shows various operations across 6 API categories:

1. Profile & Identity APIs - User profiles, connections
2. Posts & Shares APIs - Creating and managing posts
3. Company & Organizations APIs - Organization data
4. Advertising APIs - Ad accounts, campaigns
5. Media & Assets APIs - Upload media
6. Analytics & Insights APIs - Statistics and metrics

Prerequisites:
- LinkedIn OAuth 2.0 access token with appropriate scopes
- Set LINKEDIN_ACCESS_TOKEN environment variable

Scopes needed (depending on operations):
- r_liteprofile: Read profile
- r_basicprofile: Read basic profile (legacy)
- w_member_social: Post on behalf of member
- r_organization_social: Read organization data
- rw_ads: Manage advertising

Example usage:
    export LINKEDIN_ACCESS_TOKEN="your_access_token_here"
    python example.py
"""

import os

from app.sources.client.linkedin.linkedin import LinkedInClient, LinkedInOAuth2Config
from app.sources.external.linkedin.linkedin import LinkedInDataSource


def test_profile_operations(linkedin: LinkedInDataSource) -> None:
    """Test profile-related operations"""
    print("\n" + "="*60)
    print("TESTING PROFILE & IDENTITY APIs (6 methods)")
    print("="*60)

    # Test 1: Get current user's profile
    print("\n1. Getting current user's profile...")
    try:
        response = linkedin.get_profile()
        print("✅ Profile retrieved successfully")
        print(f"   Status: {response.status_code}")
        print(f"   Profile ID: {response.entity.get('id')}")
        print(f"   Name: {response.entity.get('firstName')} {response.entity.get('lastName')}")
        print(f"   Entity keys: {list(response.entity.keys())}")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Test 2: Get profile with field projection
    print("\n2. Getting profile with specific fields...")
    try:
        response = linkedin.get_profile(
            query_params={"fields": "id,firstName,lastName"}
        )
        print("✅ Profile with projection retrieved")
        print(f"   Data: {response.entity}")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Test 3: Get profile with image decoration
    print("\n3. Getting profile with image decoration...")
    try:
        response = linkedin.get_profile_with_decoration()
        print("✅ Profile with decoration retrieved")
        print(f"   Has profile picture: {'profilePicture' in response.entity}")
    except Exception as e:
        print(f"❌ Error: {e}")


def test_organization_operations(linkedin: LinkedInDataSource) -> None:
    """Test organization-related operations"""
    print("\n" + "="*60)
    print("TESTING COMPANY & ORGANIZATIONS APIs (8 methods)")
    print("="*60)

    # Test 1: Get accessible organizations
    print("\n1. Getting accessible organizations...")
    try:
        response = linkedin.get_organizations()
        print("✅ Organizations retrieved successfully")
        print(f"   Status: {response.status_code}")
        if hasattr(response, 'elements') and response.elements:
            print(f"   Count: {len(response.elements)}")
            if response.elements:
                first_org = response.elements[0]
                print(f"   First org: {first_org.get('id')} - {first_org.get('localizedName')}")
                org_id = first_org.get('id')

                # Test 2: Get specific organization
                print(f"\n2. Getting organization details (ID: {org_id})...")
                try:
                    org_response = linkedin.get_organization(org_id)
                    print("✅ Organization retrieved")
                    print(f"   Name: {org_response.entity.get('localizedName')}")
                    print(f"   Website: {org_response.entity.get('websiteUrl')}")
                except Exception as e:
                    print(f"❌ Error: {e}")
        else:
            print("   No organizations found (may need r_organization_social scope)")
    except Exception as e:
        print(f"❌ Error: {e}")

    # Test 3: Search organizations
    print("\n3. Searching organizations by keyword...")
    try:
        response = linkedin.search_organizations(
            keywords="technology",
            query_params={"count": 5}
        )
        print("✅ Organization search completed")
        print(f"   Results: {len(response.elements) if hasattr(response, 'elements') else 0}")
    except Exception as e:
        print(f"❌ Error: {e} (may need r_organization_social scope)")


def test_posts_operations(linkedin: LinkedInDataSource) -> None:
    """Test posts and shares operations"""
    print("\n" + "="*60)
    print("TESTING POSTS & SHARES APIs (11 methods)")
    print("="*60)

    # Get person URN first
    print("\n1. Getting person URN for post creation...")
    try:
        profile = linkedin.get_profile()
        person_id = profile.entity.get('id')
        person_urn = f"urn:li:person:{person_id}"
        print(f"✅ Person URN: {person_urn}")

        # Example: Create a post (COMMENTED OUT - uncomment to actually post)
        print("\n2. Create post demo (COMMENTED OUT - uncomment to test)...")
        print("   Code to create a post:")
        print("   response = linkedin.create_post(")
        print(f"       author='{person_urn}',")
        print("       commentary='Test post from LinkedIn SDK!',")
        print("       visibility='PUBLIC'")
        print("   )")
        print("   ⚠️ Uncomment in code to actually create a post")

        # Uncomment below to actually create a post:
        # response = linkedin.create_post(
        #     author=person_urn,
        #     commentary="Test post from LinkedIn official SDK integration! 🚀",
        #     visibility="PUBLIC"
        # )
        # print(f"✅ Post created successfully")
        # print(f"   Post ID: {response.entity_id}")

        # Example: Create UGC post (legacy API)
        print("\n3. Create UGC post demo (COMMENTED OUT)...")
        print("   response = linkedin.create_ugc_post(...)")
        print("   ⚠️ Legacy /ugcPosts API - use create_post() for new posts")

    except Exception as e:
        print(f"❌ Error: {e}")


def test_advertising_operations(linkedin: LinkedInDataSource) -> None:
    """Test advertising operations"""
    print("\n" + "="*60)
    print("TESTING ADVERTISING APIs (15 methods)")
    print("="*60)

    print("\n1. Searching ad accounts...")
    try:
        response = linkedin.search_ad_accounts(
            search_params={
                "test": True,
                "status": {"values": ["ACTIVE", "DRAFT"]}
            },
            paging_params={"start": 0, "count": 10}
        )
        print("✅ Ad accounts search completed")
        print(f"   Status: {response.status_code}")
        print(f"   Results: {len(response.elements) if hasattr(response, 'elements') else 0}")
        print("   ⚠️ Requires r_ads or rw_ads scope")
    except Exception as e:
        print(f"❌ Error: {e} (may need r_ads/rw_ads scope)")

    print("\n2. Ad account operations available:")
    print("   - create_ad_account()")
    print("   - get_ad_account(account_id)")
    print("   - update_ad_account(account_id, patch_data)")
    print("   - delete_ad_account(account_id)")
    print("   - search_campaigns(account_urn)")
    print("   - create_campaign(campaign_data)")
    print("   - batch_get_campaigns(campaign_ids)")
    print("   - get_ad_analytics(finder_params)")
    print("   ... and 7 more advertising methods")


def test_analytics_operations(linkedin: LinkedInDataSource) -> None:
    """Test analytics-related operations"""
    print("\n" + "="*60)
    print("TESTING ANALYTICS & INSIGHTS APIs (6 methods)")
    print("="*60)

    # Try to get organization for analytics
    print("\n1. Getting organization follower statistics...")
    try:
        orgs = linkedin.get_organizations()
        if hasattr(orgs, 'elements') and orgs.elements:
            org_id = orgs.elements[0].get('id')
            org_urn = f"urn:li:organization:{org_id}"

            response = linkedin.get_organization_follower_statistics(
                org_urn=org_urn,
                time_ranges=[{
                    "start": 1609459200000,  # Jan 1, 2021
                    "end": 1640995200000     # Dec 31, 2021
                }]
            )
            print("✅ Follower statistics retrieved")
            print(f"   Status: {response.status_code}")
        else:
            print("⚠️  No organizations available for analytics test")
    except Exception as e:
        print(f"❌ Error: {e} (may need r_organization_social scope)")

    print("\n2. Analytics operations available:")
    print("   - get_follower_statistics(org_urn, time_ranges)")
    print("   - batch_get_share_statistics(share_urns)")
    print("   - get_page_statistics(org_urn, time_ranges)")
    print("   - get_visitor_analytics(org_urn, time_ranges)")
    print("   - get_engagement_metrics(entity_urn)")


def test_media_operations(linkedin: LinkedInDataSource) -> None:
    """Test media and assets operations"""
    print("\n" + "="*60)
    print("TESTING MEDIA & ASSETS APIs (4 methods)")
    print("="*60)

    print("\n1. Media upload operations available:")
    print("   - register_upload(owner, recipes) - Get upload URL")
    print("   - register_video_upload(owner, file_size) - Video upload")
    print("   - get_asset(asset_id) - Get asset metadata")
    print("   - delete_asset(asset_id) - Delete asset")

    print("\n2. Media upload workflow:")
    print("   Step 1: Call register_upload() to get upload URL")
    print("   Step 2: Use HTTP PUT to upload binary data to URL")
    print("   Step 3: Use asset URN in post creation")
    print("   ⚠️ Requires w_member_social or w_organization_social scope")

    # Example of getting upload URL (not actually uploading)
    print("\n3. Register upload demo (COMMENTED OUT)...")
    print("   Code to register upload:")
    print("   response = linkedin.register_upload(")
    print("       owner='urn:li:person:XXX',")
    print("       recipes=['urn:li:digitalmediaRecipe:feedshare-image']")
    print("   )")
    print("   upload_url = response.value['uploadMechanism']['...']['uploadUrl']")


def main() -> None:
    """Main function to run all tests"""
    # Get access token from environment
    access_token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    api_version = os.getenv("LINKEDIN_API_VERSION", "202406")  # Default to 202406

    if not access_token:
        print("❌ Error: LINKEDIN_ACCESS_TOKEN environment variable not set")
        print("\nTo use this example:")
        print("1. Get a LinkedIn OAuth 2.0 access token from:")
        print("   https://www.linkedin.com/developers/tools/oauth/token-generator")
        print("\n2. Set the environment variable:")
        print("   Windows PowerShell:")
        print('     $env:LINKEDIN_ACCESS_TOKEN="your_access_token_here"')
        print("   Linux/Mac:")
        print('     export LINKEDIN_ACCESS_TOKEN="your_access_token_here"')
        print("\n3. Optionally set API version (default: 202406):")
        print('     $env:LINKEDIN_API_VERSION="202406"')
        print("\n4. Run this script again:")
        print("     python example.py")
        print("\nRequired scopes depend on operations:")
        print("   - r_liteprofile: Read profile")
        print("   - w_member_social: Create posts")
        print("   - r_organization_social: Read organization data")
        print("   - r_ads/rw_ads: Advertising operations")
        return

    print("="*60)
    print("LINKEDIN DATA SOURCE - OFFICIAL SDK INTEGRATION")
    print("="*60)
    print("\nSDK: linkedin-api-client (RestliClient)")
    print(f"API Version: {api_version}")
    print(f"Access token: {access_token[:20]}...{access_token[-5:]}" if len(access_token) > 25 else "***")

    # Create LinkedIn client with official SDK
    linkedin_config = LinkedInOAuth2Config(
        access_token=access_token,
        version_string=api_version
    )
    linkedin_client = LinkedInClient.build_with_config(linkedin_config)
    print("✅ LinkedIn client created (wrapping official RestliClient)")

    # Create LinkedIn data source
    linkedin_data_source = LinkedInDataSource(linkedin_client)
    print("✅ LinkedIn data source created (50+ API methods)")

    try:
        # Run all test operations
        print("\n" + "="*60)
        print("TESTING 6 API CATEGORIES (50+ METHODS)")
        print("="*60)

        test_profile_operations(linkedin_data_source)
        test_posts_operations(linkedin_data_source)
        test_organization_operations(linkedin_data_source)
        test_advertising_operations(linkedin_data_source)
        test_media_operations(linkedin_data_source)
        test_analytics_operations(linkedin_data_source)

        print("\n" + "="*60)
        print("ALL TESTS COMPLETED")
        print("="*60)
        print("\n✅ Successfully demonstrated official SDK integration")
        print("\nAPI Coverage:")
        print("   - Profile & Identity: 6 methods")
        print("   - Posts & Shares: 11 methods")
        print("   - Company & Organizations: 8 methods")
        print("   - Advertising: 15 methods")
        print("   - Media & Assets: 4 methods")
        print("   - Analytics & Insights: 6 methods")
        print("   Total: 50+ methods")

        print("\nKey Features:")
        print("   ✅ Official linkedin-api-client SDK")
        print("   ✅ Native SDK response objects")
        print("   ✅ Proper Rest.li protocol support")
        print("   ✅ Automatic query tunneling")
        print("   ✅ Type-safe (no typing.Any)")
        print("   ✅ Simplified response handling")

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*60)
    print("Example completed - check output above for results")
    print("="*60)


if __name__ == "__main__":
    main()
