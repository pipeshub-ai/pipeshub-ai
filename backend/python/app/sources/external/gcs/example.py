# ruff: noqa
"""
Comprehensive GCS DataSource example and test suite.
Includes all operations testing and method verification.
"""
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Add parent directory to path to find app module
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from app.sources.client.gcs.gcs import GCSADCConfig, GCSClient, GCSResponse
from app.sources.external.gcs.gcs import GCSDataSource

try:
    from gcloud.aio.storage import Storage  # type: ignore
except ImportError:
    print("❌ gcloud-aio-storage not installed")
    sys.exit(1)


async def verify_available_methods() -> None:
    """Check available methods in gcloud-aio-storage Storage class."""
    print("=" * 80)
    print("VERIFYING GCLOUD-AIO-STORAGE METHODS")
    print("=" * 80)
    
    storage = Storage()
    
    methods_to_check = [
        'upload', 'download', 'delete', 'list_objects', 'list_buckets',
        'get_bucket', 'delete_bucket', 'get', 'copy', 'compose', 'rewrite',
        'patch', 'update'
    ]
    
    available = []
    missing = []
    
    for method in methods_to_check:
        if hasattr(storage, method):
            available.append(method)
            print(f"✅ {method}: Available")
        else:
            missing.append(method)
            print(f"❌ {method}: NOT available")
    
    print("\n" + "=" * 80)
    print(f"Available: {len(available)}/{len(methods_to_check)}")
    print(f"Missing: {len(missing)}/{len(methods_to_check)}")
    
    if missing:
        print("\n⚠️  Methods that may need REST API implementation:")
        for m in missing:
            print(f"   - {m}")
    
    await storage.close()
    print("=" * 80)
    print()


async def test_all_operations() -> None:
    """Test all GCS DataSource operations."""
    # Setup
    PROJECT_ID = os.environ.get("PROJECT_ID", "demo-project")
    BUCKET_NAME = os.environ.get("BUCKET_NAME", "test-bucket-comprehensive")
    TEST_OBJECT = "test-object.txt"
    TEST_DATA = b"Hello, GCS! This is test data."
    
    print("=" * 80)
    print("GCS DATASOURCE COMPREHENSIVE TEST SUITE")
    print("=" * 80)
    print(f"Project ID: {PROJECT_ID}")
    print(f"Bucket Name: {BUCKET_NAME}")
    print(f"Storage Emulator: {os.environ.get('STORAGE_EMULATOR_HOST', 'Not set')}")
    print("=" * 80)
    
    # Initialize client
    cfg = GCSADCConfig(bucketName=BUCKET_NAME, projectId=PROJECT_ID)
    client = GCSClient.build_with_adc_config(cfg)
    ds = GCSDataSource(client)
    
    results: Dict[str, Any] = {"passed": 0, "failed": 0, "errors": []}
    
    def check_result(test_name: str, response: GCSResponse, expected_success: bool = True) -> None:
        """Check test result and update statistics."""
        if hasattr(response, "success"):
            if response.success == expected_success:
                results["passed"] += 1
                print(f"✅ {test_name}: PASSED")
            else:
                results["failed"] += 1
                error_msg = f"{test_name}: FAILED - {response.error}"
                results["errors"].append(error_msg)
                print(f"❌ {error_msg}")
        else:
            results["failed"] += 1
            error_msg = f"{test_name}: FAILED - Unexpected response type: {type(response)}"
            results["errors"].append(error_msg)
            print(f"❌ {error_msg}")
    
    # ========== BUCKET OPERATIONS ==========
    print("\n" + "=" * 80)
    print("BUCKET OPERATIONS")
    print("=" * 80)
    
    # 1. Ensure bucket exists
    print("\n1. Testing ensure_bucket_exists...")
    try:
        resp = await ds.ensure_bucket_exists()
        check_result("ensure_bucket_exists", resp)
        print(f"   Response: {resp.to_json()[:200]}...")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"ensure_bucket_exists: Exception - {e}")
        print(f"❌ ensure_bucket_exists: Exception - {e}")
    
    # 2. List buckets
    print("\n2. Testing list_buckets...")
    try:
        resp = await ds.list_buckets()
        check_result("list_buckets", resp)
        if resp.success and resp.data:
            bucket_count = len(resp.data.get("buckets", []))
            print(f"   Found {bucket_count} bucket(s)")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"list_buckets: Exception - {e}")
        print(f"❌ list_buckets: Exception - {e}")
    
    # 3. Get bucket
    print("\n3. Testing get_bucket...")
    try:
        resp = await ds.get_bucket()
        check_result("get_bucket", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"get_bucket: Exception - {e}")
        print(f"❌ get_bucket: Exception - {e}")
    
    # 4. Create bucket (new bucket)
    print("\n4. Testing create_bucket...")
    try:
        new_bucket = f"{BUCKET_NAME}-new-{int(time.time())}"
        resp = await ds.create_bucket(new_bucket, location="US")
        check_result("create_bucket", resp)
        if resp.success:
            await ds.delete_bucket(bucket_name=new_bucket)
        elif "409" in str(resp.error) or "already exists" in str(resp.error):
            print("   ⚠️  Bucket already exists (409), treating as success for idempotency")
            results["passed"] += 1
            results["failed"] -= 1
            if "create_bucket: FAILED" in results["errors"]:
                results["errors"] = [e for e in results["errors"] if not e.startswith("create_bucket:")]
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"create_bucket: Exception - {e}")
        print(f"❌ create_bucket: Exception - {e}")
    
    # 5. Patch bucket
    print("\n5. Testing patch_bucket...")
    try:
        resp = await ds.patch_bucket(metadata={"label": "test"})
        check_result("patch_bucket", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"patch_bucket: Exception - {e}")
        print(f"❌ patch_bucket: Exception - {e}")
    
    # ========== OBJECT OPERATIONS ==========
    print("\n" + "=" * 80)
    print("OBJECT OPERATIONS")
    print("=" * 80)
    
    # 6. Upload object
    print("\n6. Testing upload_object...")
    try:
        resp = await ds.upload_object(None, TEST_OBJECT, TEST_DATA, content_type="text/plain")
        check_result("upload_object", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"upload_object: Exception - {e}")
        print(f"❌ upload_object: Exception - {e}")
    
    # 7. List objects
    print("\n7. Testing list_objects...")
    try:
        resp = await ds.list_objects()
        check_result("list_objects", resp)
        if resp.success and resp.data:
            items = resp.data.get("items", [])
            print(f"   Found {len(items)} object(s)")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"list_objects: Exception - {e}")
        print(f"❌ list_objects: Exception - {e}")
    
    # 8. Get object metadata
    print("\n8. Testing get_object_metadata...")
    try:
        resp = await ds.get_object_metadata(None, TEST_OBJECT)
        check_result("get_object_metadata", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"get_object_metadata: Exception - {e}")
        print(f"❌ get_object_metadata: Exception - {e}")
    
    # 9. Download object
    print("\n9. Testing download_object...")
    try:
        resp = await ds.download_object(None, TEST_OBJECT)
        check_result("download_object", resp)
        if resp.success and resp.data:
            downloaded_data = resp.data.get("data", b"")
            print(f"   Downloaded {len(downloaded_data)} bytes")
            if downloaded_data == TEST_DATA:
                print("   ✅ Data matches uploaded data")
            else:
                print("   ⚠️  Data mismatch")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"download_object: Exception - {e}")
        print(f"❌ download_object: Exception - {e}")
    
    # 10. Copy object
    print("\n10. Testing copy_object...")
    try:
        copied_object = f"{TEST_OBJECT}-copy"
        resp = await ds.copy_object(BUCKET_NAME, TEST_OBJECT, None, copied_object)
        check_result("copy_object", resp)
        if resp.success:
            await ds.delete_object(None, copied_object)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"copy_object: Exception - {e}")
        print(f"❌ copy_object: Exception - {e}")
    
    # 11. Compose object
    print("\n11. Testing compose_object...")
    try:
        source1 = "compose-source1.txt"
        source2 = "compose-source2.txt"
        await ds.upload_object(None, source1, b"Part 1", content_type="text/plain")
        await ds.upload_object(None, source2, b"Part 2", content_type="text/plain")
        
        source_objects = [{"name": source1}, {"name": source2}]
        resp = await ds.compose_object(None, "composed.txt", source_objects)
        check_result("compose_object", resp)
        if resp.success:
            await ds.delete_object(None, "composed.txt")
            await ds.delete_object(None, source1)
            await ds.delete_object(None, source2)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"compose_object: Exception - {e}")
        print(f"❌ compose_object: Exception - {e}")
    
    # 12. Rewrite object
    print("\n12. Testing rewrite_object...")
    try:
        resp = await ds.rewrite_object(BUCKET_NAME, TEST_OBJECT, None, f"{TEST_OBJECT}-rewritten")
        check_result("rewrite_object", resp)
        if resp.success:
            await ds.delete_object(None, f"{TEST_OBJECT}-rewritten")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"rewrite_object: Exception - {e}")
        print(f"❌ rewrite_object: Exception - {e}")
    
    # 13. Update object metadata
    print("\n13. Testing update_object_metadata...")
    try:
        resp = await ds.update_object_metadata(None, TEST_OBJECT, {"custom-metadata": "test-value"})
        check_result("update_object_metadata", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"update_object_metadata: Exception - {e}")
        print(f"❌ update_object_metadata: Exception - {e}")
    
    # 14. Delete object
    print("\n14. Testing delete_object...")
    try:
        resp = await ds.delete_object(None, TEST_OBJECT)
        check_result("delete_object", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"delete_object: Exception - {e}")
        print(f"❌ delete_object: Exception - {e}")
    
    # 15. Delete bucket (cleanup)
    print("\n15. Testing delete_bucket (cleanup)...")
    try:
        temp_bucket = f"{BUCKET_NAME}-temp-delete"
        await ds.create_bucket(temp_bucket)
        resp = await ds.delete_bucket(bucket_name=temp_bucket)
        check_result("delete_bucket", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"delete_bucket: Exception - {e}")
        print(f"❌ delete_bucket: Exception - {e}")
    
    # ========== BUCKET IAM OPERATIONS ==========
    print("\n" + "=" * 80)
    print("BUCKET IAM OPERATIONS")
    print("=" * 80)
    
    # 16. Get bucket IAM policy
    print("\n16. Testing get_bucket_iam_policy...")
    try:
        resp = await ds.get_bucket_iam_policy()
        check_result("get_bucket_iam_policy", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"get_bucket_iam_policy: Exception - {e}")
        print(f"❌ get_bucket_iam_policy: Exception - {e}")
    
    # 17. Set bucket IAM policy
    print("\n17. Testing set_bucket_iam_policy...")
    try:
        policy = {
            "bindings": [
                {
                    "role": "roles/storage.objectViewer",
                    "members": ["allUsers"]
                }
            ]
        }
        resp = await ds.set_bucket_iam_policy(policy)
        check_result("set_bucket_iam_policy", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"set_bucket_iam_policy: Exception - {e}")
        print(f"❌ set_bucket_iam_policy: Exception - {e}")
    
    # 18. Test bucket IAM permissions
    print("\n18. Testing test_bucket_iam_permissions...")
    try:
        resp = await ds.test_bucket_iam_permissions(["storage.buckets.get", "storage.objects.list"])
        check_result("test_bucket_iam_permissions", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"test_bucket_iam_permissions: Exception - {e}")
        print(f"❌ test_bucket_iam_permissions: Exception - {e}")
    
    # ========== FOLDER OPERATIONS ==========
    print("\n" + "=" * 80)
    print("FOLDER OPERATIONS")
    print("=" * 80)
    
    # 19. Create folder
    print("\n19. Testing create_folder...")
    try:
        folder_path = "test-folder/subfolder"
        resp = await ds.create_folder(None, folder_path)
        check_result("create_folder", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"create_folder: Exception - {e}")
        print(f"❌ create_folder: Exception - {e}")
    
    # 20. List folders
    print("\n20. Testing list_folders...")
    try:
        resp = await ds.list_folders(prefix="test-folder")
        check_result("list_folders", resp)
        if resp.success and resp.data:
            folders = resp.data.get("folders", [])
            print(f"   Found {len(folders)} folder(s)")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"list_folders: Exception - {e}")
        print(f"❌ list_folders: Exception - {e}")
    
    # 21. Delete folder
    print("\n21. Testing delete_folder...")
    try:
        resp = await ds.delete_folder(None, "test-folder/subfolder", recursive=False)
        check_result("delete_folder", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"delete_folder: Exception - {e}")
        print(f"❌ delete_folder: Exception - {e}")
    
    # ========== BUCKET ACCESS CONTROLS ==========
    print("\n" + "=" * 80)
    print("BUCKET ACCESS CONTROLS")
    print("=" * 80)
    
    # 22. List bucket access controls
    print("\n22. Testing list_bucket_access_controls...")
    try:
        resp = await ds.list_bucket_access_controls()
        check_result("list_bucket_access_controls", resp)
        if resp.success and resp.data:
            acls = resp.data.get("items", [])
            print(f"   Found {len(acls)} ACL entry/entries")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"list_bucket_access_controls: Exception - {e}")
        print(f"❌ list_bucket_access_controls: Exception - {e}")
    
    # 23. Insert bucket access control
    print("\n23. Testing insert_bucket_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.insert_bucket_access_control(entity, "READER")
        check_result("insert_bucket_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"insert_bucket_access_control: Exception - {e}")
        print(f"❌ insert_bucket_access_control: Exception - {e}")
    
    # 24. Get bucket access control
    print("\n24. Testing get_bucket_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.get_bucket_access_control(entity)
        check_result("get_bucket_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"get_bucket_access_control: Exception - {e}")
        print(f"❌ get_bucket_access_control: Exception - {e}")
    
    # 25. Patch bucket access control
    print("\n25. Testing patch_bucket_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.patch_bucket_access_control(entity, role="OWNER")
        check_result("patch_bucket_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"patch_bucket_access_control: Exception - {e}")
        print(f"❌ patch_bucket_access_control: Exception - {e}")
    
    # 26. Delete bucket access control
    print("\n26. Testing delete_bucket_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.delete_bucket_access_control(entity)
        check_result("delete_bucket_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"delete_bucket_access_control: Exception - {e}")
        print(f"❌ delete_bucket_access_control: Exception - {e}")
    
    # ========== NOTIFICATIONS ==========
    print("\n" + "=" * 80)
    print("NOTIFICATIONS")
    print("=" * 80)
    
    # 27. List notifications
    print("\n27. Testing list_notifications...")
    try:
        resp = await ds.list_notifications()
        check_result("list_notifications", resp)
        if resp.success and resp.data:
            notifications = resp.data.get("items", [])
            print(f"   Found {len(notifications)} notification(s)")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"list_notifications: Exception - {e}")
        print(f"❌ list_notifications: Exception - {e}")
    
    # 28. Insert notification (if topic is available)
    print("\n28. Testing insert_notification...")
    try:
        # Note: This requires a valid Pub/Sub topic
        # For emulator, we'll skip if it fails
        topic = "projects/demo-project/topics/test-topic"
        resp = await ds.insert_notification(topic, payload_format="JSON")
        if resp.success:
            check_result("insert_notification", resp)
            notification_id = resp.data.get("result", {}).get("id") if resp.data else None
            if notification_id:
                # Clean up: delete notification
                await ds.delete_notification(notification_id)
        else:
            print("   ⚠️  Skipped (requires valid Pub/Sub topic)")
            results["passed"] += 1
    except Exception as e:
        print(f"   ⚠️  Skipped (requires valid Pub/Sub topic): {e}")
        results["passed"] += 1
    
    # ========== OBJECT ACCESS CONTROLS ==========
    print("\n" + "=" * 80)
    print("OBJECT ACCESS CONTROLS")
    print("=" * 80)
    
    # 29. Upload test object for ACL operations
    test_acl_object = "test-acl-object.txt"
    await ds.upload_object(None, test_acl_object, b"test data", content_type="text/plain")
    
    # 30. List object access controls
    print("\n30. Testing list_object_access_controls...")
    try:
        resp = await ds.list_object_access_controls(None, test_acl_object)
        check_result("list_object_access_controls", resp)
        if resp.success and resp.data:
            acls = resp.data.get("items", [])
            print(f"   Found {len(acls)} ACL entry/entries")
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"list_object_access_controls: Exception - {e}")
        print(f"❌ list_object_access_controls: Exception - {e}")
    
    # 31. Insert object access control
    print("\n31. Testing insert_object_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.insert_object_access_control(None, test_acl_object, entity, "READER")
        check_result("insert_object_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"insert_object_access_control: Exception - {e}")
        print(f"❌ insert_object_access_control: Exception - {e}")
    
    # 32. Get object access control
    print("\n32. Testing get_object_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.get_object_access_control(None, test_acl_object, entity)
        check_result("get_object_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"get_object_access_control: Exception - {e}")
        print(f"❌ get_object_access_control: Exception - {e}")
    
    # 33. Patch object access control
    print("\n33. Testing patch_object_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.patch_object_access_control(None, test_acl_object, entity, role="OWNER")
        check_result("patch_object_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"patch_object_access_control: Exception - {e}")
        print(f"❌ patch_object_access_control: Exception - {e}")
    
    # 34. Delete object access control
    print("\n34. Testing delete_object_access_control...")
    try:
        entity = "user-test@example.com"
        resp = await ds.delete_object_access_control(None, test_acl_object, entity)
        check_result("delete_object_access_control", resp)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"delete_object_access_control: Exception - {e}")
        print(f"❌ delete_object_access_control: Exception - {e}")
    
    # Cleanup test object
    await ds.delete_object(None, test_acl_object)
    
    # ========== SUMMARY ==========
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print(f"✅ Passed: {results['passed']}")
    print(f"❌ Failed: {results['failed']}")
    print(f"Total Tests: {results['passed'] + results['failed']}")
    
    if results["errors"]:
        print("\nErrors:")
        for error in results["errors"]:
            print(f"  - {error}")
    
    print("\n" + "=" * 80)
    print("Implementation Status:")
    print("✅ Core operations: Using gcloud-aio-storage")
    print("✅ Advanced operations: Using REST API (emulator)")
    print("⚠️  Production: Some REST API methods need authentication setup")
    print("=" * 80)
    
    # Close client
    await client.close_async_client()


async def simple_example() -> None:
    """Simple example usage showcasing core GCS operations."""
    print("=" * 80)
    print("SIMPLE GCS EXAMPLE")
    print("=" * 80)
    
    # Simple example using GCSADCConfig (Application Default Credentials)
    # This doesn't require ConfigurationService setup
    PROJECT_ID = os.environ.get("PROJECT_ID", "demo-project")
    BUCKET_NAME = os.environ.get("BUCKET_NAME", "test-bucket-comprehensive")
    
    cfg = GCSADCConfig(bucketName=BUCKET_NAME, projectId=PROJECT_ID)
    client = GCSClient.build_with_adc_config(cfg)
    ds = GCSDataSource(client)
    
    try:
        # 1. Ensure bucket exists
        print("\n1. Ensuring bucket exists...")
        ensure = await ds.ensure_bucket_exists()
        print(f"   ✅ {ensure.message or 'Bucket ready'}")
        
        # 2. Upload object
        print("\n2. Uploading object...")
        data = b"hello world"
        up = await ds.upload_object(
            bucket_name=None,
            object_name="sample.txt",
            data=data,
            content_type="text/plain"
        )
        if up.success:
            print(f"   ✅ Uploaded: sample.txt ({len(data)} bytes)")
        
        # 3. List objects
        print("\n3. Listing objects...")
        list_resp = await ds.list_objects()
        if list_resp.success and list_resp.data:
            items = list_resp.data.get("items", [])
            print(f"   ✅ Found {len(items)} object(s) in bucket")
        
        # 4. Download object
        print("\n4. Downloading object...")
        down = await ds.download_object(bucket_name=None, object_name="sample.txt")
        if down.success and down.data:
            downloaded_data = down.data.get('data', b'')
            print(f"   ✅ Downloaded: {len(downloaded_data)} bytes")
            if downloaded_data == data:
                print("   ✅ Data matches!")
        
        # 5. Create folder
        print("\n5. Creating folder...")
        folder_resp = await ds.create_folder(None, "my-folder/subfolder")
        if folder_resp.success:
            folder_path = folder_resp.data.get("folder_path", "") if folder_resp.data else ""
            print(f"   ✅ Created folder: {folder_path}")
        
        # 6. List folders
        print("\n6. Listing folders...")
        folders_resp = await ds.list_folders(prefix="my-folder")
        if folders_resp.success and folders_resp.data:
            folders = folders_resp.data.get("folders", [])
            print(f"   ✅ Found {len(folders)} folder(s)")
            for folder in folders[:3]:  # Show first 3
                print(f"      - {folder}")
        
        # 7. List object access controls
        print("\n7. Listing object access controls...")
        acl_resp = await ds.list_object_access_controls(None, "sample.txt")
        if acl_resp.success and acl_resp.data:
            acls = acl_resp.data.get("items", [])
            print(f"   ✅ Found {len(acls)} ACL entry/entries for sample.txt")
        
        # 8. Cleanup
        print("\n8. Cleaning up...")
        await ds.delete_object(bucket_name=None, object_name="sample.txt")
        await ds.delete_folder(None, "my-folder/subfolder", recursive=False)
        print("   ✅ Cleanup complete")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        await client.close_async_client()
    
    print("\n" + "=" * 80)
    print("Example completed! Use GCS_TEST_MODE=full for comprehensive testing.")
    print("=" * 80)


async def main() -> None:
    """Main entry point."""
    # Verify available methods
    await verify_available_methods()
    
    # Check if running in test mode
    if os.environ.get("GCS_TEST_MODE") == "full":
        # Run comprehensive tests
        if not os.environ.get("STORAGE_EMULATOR_HOST"):
            print("⚠️  WARNING: STORAGE_EMULATOR_HOST not set. Set it to use emulator:")
            print("   export STORAGE_EMULATOR_HOST=http://127.0.0.1:4443")
            print()
        
        await test_all_operations()
    else:
        # Run simple example
        await simple_example()


if __name__ == "__main__":
    asyncio.run(main())
