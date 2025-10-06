"""LinkedIn API Test - All OpenID Connect Methods"""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# HTTP status code threshold for success
HTTP_SUCCESS_THRESHOLD = 400

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent.parent.parent / ".env")
except Exception:
    pass

def call_api(endpoint: str, token: str, method: str = "GET", data: Optional[dict] = None, headers_override: Optional[dict] = None) -> dict:
    headers = headers_override or {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"https://api.linkedin.com{endpoint}"
    r = requests.request(method, url, headers=headers, json=data)
    return {"status": r.status_code, "ok": r.status_code < HTTP_SUCCESS_THRESHOLD, "data": r.json() if r.text and 'application/json' in r.headers.get('content-type', '') else r.text}

def test_userinfo(token: str) -> Optional[str]:
    """Get user profile information via OpenID Connect"""
    print("\n1. TESTING: User Info (/v2/userinfo)")
    r = call_api("/v2/userinfo", token)
    if r["ok"]:
        data = r['data']
        print(f"   ✅ {r['status']} - Name: {data.get('name')}")
        print(f"      Email: {data.get('email')}")
        print(f"      User ID: {data.get('sub')}")
        print(f"      Locale: {data.get('locale')}")
        return data.get('sub')
    print(f"   ❌ {r['status']} - {r['data']}")
    return None

def test_media_upload(token: str, user_id: str) -> Optional[str]:
    """Register an image upload (Step 1 of media upload process)"""
    print("\n2. TESTING: Media Upload Registration (/v2/assets?action=registerUpload)")
    data = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": f"urn:li:person:{user_id}",
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent"
            }]
        }
    }
    r = call_api("/v2/assets?action=registerUpload", token, "POST", data)
    if r["ok"]:
        print(f"   ✅ {r['status']} - Upload registered")
        asset_id = r['data']['value']['asset']
        print(f"      Asset ID: {asset_id}")
        return asset_id
    print(f"   ❌ {r['status']} - {r['data']}")
    return None

def test_video_upload_registration(token: str, user_id: str) -> Optional[str]:
    """Register a video upload (Step 1 of video upload process)"""
    print("\n3. TESTING: Video Upload Registration (/v2/assets?action=registerUpload)")
    data = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
            "owner": f"urn:li:person:{user_id}",
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent"
            }]
        }
    }
    r = call_api("/v2/assets?action=registerUpload", token, "POST", data)
    if r["ok"]:
        print(f"   ✅ {r['status']} - Video upload registered")
        return r['data']['value']['asset']
    print(f"   ❌ {r['status']} - {r['data']}")
    return None

def test_ugc_post_creation(token: str, user_id: str, asset_id: Optional[str] = None) -> Optional[str]:
    """Create a UGC post (requires w_member_social scope)"""
    print("\n4. TESTING: Create UGC Post (/v2/ugcPosts)")

    # Add timestamp to avoid duplicate detection
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    post_data = {
        "author": f"urn:li:person:{user_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {
                    "text": f"Test post from LinkedIn OpenID Connect API 🚀 [{timestamp}]"
                },
                "shareMediaCategory": "NONE"
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }

    # Add media if asset provided
    if asset_id:
        post_data["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "IMAGE"
        post_data["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [{
            "status": "READY",
            "media": asset_id
        }]

    r = call_api("/v2/ugcPosts", token, "POST", post_data)
    if r["ok"]:
        post_id = r['data'].get('id')
        print(f"   ✅ {r['status']} - Post created: {post_id}")
        return post_id
    print(f"   ❌ {r['status']} - {r['data']}")
    return None

def test_multi_image_upload(token: str, user_id: str) -> Optional[list]:
    """Register multiple images for carousel post"""
    print("\n5. TESTING: Multi-Image Upload Registration (Carousel)")
    assets = []
    for i in range(2):
        data = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": f"urn:li:person:{user_id}",
                "serviceRelationships": [{
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent"
                }]
            }
        }
        r = call_api("/v2/assets?action=registerUpload", token, "POST", data)
        if r["ok"]:
            assets.append(r['data']['value']['asset'])

    if assets:
        print(f"   ✅ Registered {len(assets)} images for carousel")
        return assets
    print("   ❌ Failed to register images")
    return None

def main() -> None:
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if not token:
        print("❌ Set LINKEDIN_ACCESS_TOKEN in .env")
        return

    print("=" * 60)
    print("LINKEDIN API TEST - All OpenID Connect Methods")
    print("=" * 60)

    # Test 1: Get user info (always works)
    user_id = test_userinfo(token)
    if not user_id:
        print("\n❌ Cannot proceed without user ID")
        return

    # Test 2: Image upload registration
    asset_id = test_media_upload(token, user_id)

    # Test 3: Video upload registration
    video_asset = test_video_upload_registration(token, user_id)

    # Test 4: Create text post (requires w_member_social scope)
    post_id = test_ugc_post_creation(token, user_id)

    # Test 5: Create post with image (if upload succeeded)
    if asset_id:
        print("\n   NOTE: To complete image post, upload image to URL, then create post")

    # Test 5: Multi-image carousel registration
    carousel_assets = test_multi_image_upload(token, user_id)

    print("\n" + "=" * 60)
    print("✅ TEST COMPLETE!")
    print("=" * 60)
    print("\nSUMMARY:")
    print(f"  User Info: {'✅' if user_id else '❌'}")
    print(f"  Image Upload: {'✅' if asset_id else '❌'}")
    print(f"  Video Upload: {'✅' if video_asset else '❌'}")
    print(f"  Post Creation: {'✅' if post_id else '❌'}")
    print(f"  Carousel Images: {'✅' if carousel_assets else '❌'}")
    print("\nNOTE: w_member_social scope required for post creation")

if __name__ == "__main__":
    main()
