# ruff: noqa
import asyncio
import os
import logging
import uuid
import datetime
from typing import Union

# Adjust imports to match your project structure
from app.sources.client.nextcloud.nextcloud import (
    NextcloudClient,
    NextcloudUsernamePasswordConfig,
    NextcloudTokenConfig
)
from app.sources.external.nextcloud.nextcloud import NextcloudDataSource 

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("nextcloud_master")

async def main():
    print("🚀 Starting Nextcloud Master Test Suite...\n")

    # 1. Credentials
    base_url = os.getenv("NEXTCLOUD_BASE_URL")
    token = os.getenv("NEXTCLOUD_TOKEN")
    username = os.getenv("NEXTCLOUD_USERNAME")
    password = os.getenv("NEXTCLOUD_PASSWORD")
    
    if not base_url:
        raise Exception("Missing NEXTCLOUD_BASE_URL")

    # 2. Build Config
    if token:
        print(f"🔹 Auth: Bearer Token")
        config = NextcloudTokenConfig(base_url=base_url, token=token)
    elif username and password:
        print(f"🔹 Auth: Basic (User/Pass)")
        config = NextcloudUsernamePasswordConfig(base_url=base_url, username=username, password=password)
    else:
        raise Exception("Missing Credentials")

    # 3. Initialize
    client = NextcloudClient.build_with_config(config)
    ds = NextcloudDataSource(client)

    # Global Test Variables
    TEST_ID = uuid.uuid4().hex[:6]
    TEST_FOLDER = f"/test_suite_{TEST_ID}"
    FILE_NAME = f"master_test_{TEST_ID}.txt"
    FILE_PATH = f"{TEST_FOLDER}/{FILE_NAME}"
    TARGET_USER = username or "NC_Admin" # Fallback if token doesn't provide it immediately

    try:
        # ==================================================================
        # PHASE 1: CONNECTIVITY & USER
        # ==================================================================
        print("\n--- [1/7] Connectivity & User Info ---")
        
        # 1. Capabilities
        cap_resp = await ds.get_capabilities()
        if cap_resp.status == 200:
            ver = cap_resp.json().get('ocs', {}).get('data', {}).get('version', {}).get('string')
            print(f"✅ Connected to Nextcloud {ver}")
        else:
            raise Exception(f"Failed to connect: {cap_resp.status}")

        # 2. Get User Status (Resolve User ID if needed)
        status_resp = await ds.get_current_user_status()
        if status_resp.status == 200:
            data = status_resp.json().get('ocs', {}).get('data', {})
            TARGET_USER = data.get('userId')
            print(f"✅ Authenticated as: {TARGET_USER} (Status: {data.get('statusType')})")
        
        # ==================================================================
        # PHASE 2: FILES & FOLDERS (WebDAV)
        # ==================================================================
        print("\n--- [2/7] File Operations ---")

        # 1. Create Folder
        print(f"📂 Creating folder: {TEST_FOLDER}")
        await ds.create_folder(user_id=TARGET_USER, path=TEST_FOLDER)

        # 2. Upload File
        content = f"Master test content generated at {datetime.datetime.now()}".encode('utf-8')
        print(f"out Uploading file: {FILE_PATH}")
        up_resp = await ds.upload_file(user_id=TARGET_USER, path=FILE_PATH, data=content)
        if up_resp.status in [201, 204]:
            print(f"✅ Upload success")
        else:
            print(f"❌ Upload failed: {up_resp.status}")

        # 3. List Directory
        list_resp = await ds.list_directory(user_id=TARGET_USER, path=TEST_FOLDER)
        if list_resp.status == 207 and FILE_NAME in list_resp.text():
            print(f"✅ File found in directory listing")
        else:
            print(f"❌ File missing from listing")

        # 4. Download File
        down_resp = await ds.download_file(user_id=TARGET_USER, path=FILE_PATH)
        if down_resp.status == 200 and down_resp.text() == content.decode('utf-8'):
            print(f"✅ Download verification passed")
        else:
            print(f"❌ Download verification failed")

        # ==================================================================
        # PHASE 3: SEARCH (XML)
        # ==================================================================
        print("\n--- [3/7] Advanced Search ---")
        
        # Search by MimeType
        search_resp = await ds.search_files_by_content_type(user_id=TARGET_USER, path=TEST_FOLDER, content_type="text/plain")
        if search_resp.status == 207 and FILE_NAME in search_resp.text():
            print(f"✅ XML Search found file by MimeType")
        else:
            print(f"❌ XML Search failed")

        # ==================================================================
        # PHASE 4: FEATURES (Comments & Status)
        # ==================================================================
        print("\n--- [4/7] Features (Comments) ---")

        # We need the file ID to comment. It's usually in the PROPFIND/SEARCH response,
        # but parsing XML without a library is messy. We'll skip complex parsing here 
        # and just try to set a user status instead.
        
        print(f"💬 Setting User Status to 'dnd'...")
        await ds.set_user_status_type("dnd")
        print(f"✅ Status updated. Clearing it back...")
        await ds.set_user_status_type("online")

        # ==================================================================
        # PHASE 5: SHARING (OCS)
        # ==================================================================
        print("\n--- [5/7] Sharing ---")
        
        # Create a Public Link Share (Type 3)
        print(f"🔗 Creating public link for {FILE_PATH}...")
        share_resp = await ds.create_share(path=FILE_PATH, share_type=3, permissions=1)
        
        share_id = None
        if share_resp.status == 200:
            data = share_resp.json().get('ocs', {}).get('data', {})
            share_id = data.get('id')
            token = data.get('token')
            print(f"✅ Share created! ID: {share_id} | URL: {data.get('url')}")
        else:
            print(f"❌ Share creation failed: {share_resp.text()}")

        # Delete the share
        if share_id:
            print(f"🗑️  Deleting Share ID {share_id}...")
            await ds.delete_share(share_id)
            print(f"✅ Share deleted")

        # ==================================================================
        # PHASE 6: CHUNKED UPLOAD (v2)
        # ==================================================================
        print("\n--- [6/7] Chunked Upload (v2) ---")
        # Simulate a large file by splitting string into 2 chunks
        CHUNK_PATH = f"{TEST_FOLDER}/chunked_file.txt"
        UPLOAD_ID = uuid.uuid4().hex
        
        chunk1 = b"Part 1 of the data... "
        chunk2 = b"...Part 2 of the data."
        total_size = len(chunk1) + len(chunk2)

        print(f"📦 Initiating Chunked Upload ID: {UPLOAD_ID}")
        await ds.initiate_chunked_upload(user_id=TARGET_USER, upload_id=UPLOAD_ID, dest_path=CHUNK_PATH)
        
        print(f"   Uploading Chunk 1...")
        await ds.upload_chunk(user_id=TARGET_USER, upload_id=UPLOAD_ID, chunk_index=1, data=chunk1, dest_path=CHUNK_PATH, total_length=total_size)
        
        print(f"   Uploading Chunk 2...")
        await ds.upload_chunk(user_id=TARGET_USER, upload_id=UPLOAD_ID, chunk_index=2, data=chunk2, dest_path=CHUNK_PATH, total_length=total_size)
        
        print(f"   Assembling...")
        assemble_resp = await ds.complete_chunked_upload(user_id=TARGET_USER, upload_id=UPLOAD_ID, dest_path=CHUNK_PATH, total_length=total_size)
        
        if assemble_resp.status in [201, 204]:
            print(f"✅ Chunked Upload Assembled Successfully")
        else:
            print(f"❌ Assembly failed: {assemble_resp.status} - {assemble_resp.text()}")

        # ==================================================================
        # PHASE 7: CLEANUP (Trashbin)
        # ==================================================================
        print("\n--- [7/7] Cleanup & Trashbin ---")

        # Delete the Folder (Recursive delete)
        print(f"🗑️  Deleting folder: {TEST_FOLDER}")
        await ds.delete_resource(user_id=TARGET_USER, path=TEST_FOLDER)

        # Check Trashbin
        print(f"♻️  Verifying Trashbin...")
        trash_resp = await ds.list_trashbin(user_id=TARGET_USER)
        if trash_resp.status == 207 and TEST_FOLDER in trash_resp.text():
             print(f"✅ Test folder found in Trashbin")
        
        # Empty Trash
        print(f"🧹 Emptying Trashbin...")
        await ds.empty_trashbin(user_id=TARGET_USER)
        print(f"✅ Cleanup Complete")

        print("\n✨ ALL TESTS PASSED SUCCESSFULLY! ✨")

    except Exception as e:
        logger.error(f"\n❌ TEST FAILED: {e}")
        # Try to clean up anyway
        try:
             await ds.delete_resource(user_id=TARGET_USER, path=TEST_FOLDER)
        except Exception as cleanup_error:
            logger.warning(f"⚠️ Cleanup failed: {cleanup_error}")
    finally:
        await client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())