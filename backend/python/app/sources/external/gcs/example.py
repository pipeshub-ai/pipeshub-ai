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
    from google.cloud import storage  # type: ignore
except ImportError:
    print("❌ google-cloud-storage not installed")
    sys.exit(1)


async def verify_available_methods() -> None:
    """Check available methods in google-cloud-storage classes."""
    print("=" * 80)
    print("VERIFYING GOOGLE-CLOUD-STORAGE METHODS")
    print("=" * 80)
    
    checks = {
        "Client": (storage.Client, ['list_buckets', 'create_bucket', 'get_bucket']),
        "Bucket": (storage.Bucket, ['blob', 'copy_blob', 'list_blobs', 'delete', 'patch', 'get_iam_policy']),
        "Blob": (storage.Blob, ['upload_from_string', 'download_as_bytes', 'compose', 'delete'])
    }

    available_count = 0
    total_count = 0

    for class_name, (cls, methods) in checks.items():
        print(f"\nChecking {class_name} methods:")
        for method in methods:
            total_count += 1
            if hasattr(cls, method):
                available_count += 1
                print(f"✅ {class_name}.{method}: Available")
            else:
                print(f"❌ {class_name}.{method}: NOT available")
    
    print("\n" + "=" * 80)
    print(f"Available: {available_count}/{total_count}")
    print("=" * 80)


async def test_all_operations() -> None:
    """Test all GCS DataSource operations."""
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
            error_msg = f"{test_name}: FAILED - Unexpected response type"
            results["errors"].append(error_msg)
            print(f"❌ {error_msg}")
    
    # ========== BUCKET OPERATIONS ==========
    print("\n--- BUCKET OPERATIONS ---")
    
    # 1. Ensure bucket exists
    resp = await ds.ensure_bucket_exists()
    check_result("ensure_bucket_exists", resp)

    # 2. List buckets
    resp = await ds.list_buckets()
    check_result("list_buckets", resp)
    
    # 3. Get bucket
    resp = await ds.get_bucket()
    check_result("get_bucket", resp)
    
    # 4. Create bucket (new bucket)
    try:
        new_bucket = f"{BUCKET_NAME}-new-{int(time.time())}"
        resp = await ds.create_bucket(new_bucket, location="US")
        check_result("create_bucket", resp)
        if resp.success:
            await ds.delete_bucket(bucket_name=new_bucket)
    except Exception as e:
        results["failed"] += 1
        results["errors"].append(f"create_bucket: {e}")

    # 5. Patch bucket
    resp = await ds.patch_bucket(metadata={"labels": {"env": "test"}})
    check_result("patch_bucket", resp)
    
    # ========== OBJECT OPERATIONS ==========
    print("\n--- OBJECT OPERATIONS ---")
    
    # 6. Upload object
    resp = await ds.upload_object(None, TEST_OBJECT, TEST_DATA, content_type="text/plain")
    check_result("upload_object", resp)
    
    # 7. List objects
    resp = await ds.list_objects()
    check_result("list_objects", resp)
    
    # 8. Get object metadata
    resp = await ds.get_object_metadata(None, TEST_OBJECT)
    check_result("get_object_metadata", resp)
    
    # 9. Download object
    resp = await ds.download_object(None, TEST_OBJECT)
    check_result("download_object", resp)
    
    # 10. Copy object
    copied_object = f"{TEST_OBJECT}-copy"
    resp = await ds.copy_object(BUCKET_NAME, TEST_OBJECT, None, copied_object)
    check_result("copy_object", resp)
    if resp.success:
        await ds.delete_object(None, copied_object)
    
    # 11. Compose object
    source1 = "compose-source1.txt"
    source2 = "compose-source2.txt"
    await ds.upload_object(None, source1, b"Part 1", content_type="text/plain")
    await ds.upload_object(None, source2, b"Part 2", content_type="text/plain")
    source_objects = [source1, source2]
    resp = await ds.compose_object(None, "composed.txt", source_objects)
    check_result("compose_object", resp)
    if resp.success:
        await ds.delete_object(None, "composed.txt")
        await ds.delete_object(None, source1)
        await ds.delete_object(None, source2)
    
    # 12. Rewrite object
    resp = await ds.rewrite_object(BUCKET_NAME, TEST_OBJECT, None, f"{TEST_OBJECT}-rewritten")
    check_result("rewrite_object", resp)
    if resp.success:
        await ds.delete_object(None, f"{TEST_OBJECT}-rewritten")
    
    # 13. Update object metadata
    resp = await ds.update_object_metadata(None, TEST_OBJECT, {"custom-metadata": "test-value"})
    check_result("update_object_metadata", resp)
    
    # 14. Delete object
    resp = await ds.delete_object(None, TEST_OBJECT)
    check_result("delete_object", resp)
    
    # 15. Delete bucket (cleanup)
    temp_bucket = f"{BUCKET_NAME}-temp-delete"
    await ds.create_bucket(temp_bucket)
    resp = await ds.delete_bucket(bucket_name=temp_bucket)
    check_result("delete_bucket", resp)
    
    # ========== IAM OPERATIONS ==========
    print("\n--- IAM OPERATIONS ---")
    
    # 16. Get bucket IAM policy
    resp = await ds.get_bucket_iam_policy()
    check_result("get_bucket_iam_policy", resp)
    
    # 17. Set bucket IAM policy
    policy = {"bindings": [{"role": "roles/storage.objectViewer", "members": ["allUsers"]}]}
    resp = await ds.set_bucket_iam_policy(policy)
    check_result("set_bucket_iam_policy", resp)
    
    # 18. Test bucket IAM permissions
    resp = await ds.test_bucket_iam_permissions(["storage.buckets.get", "storage.objects.list"])
    check_result("test_bucket_iam_permissions", resp)
    
    # ========== FOLDER OPERATIONS ==========
    print("\n--- FOLDER OPERATIONS ---")
    
    # 19. Create folder
    folder_path = "test-folder/subfolder"
    resp = await ds.create_folder(None, folder_path)
    check_result("create_folder", resp)
    
    # 20. List folders
    resp = await ds.list_folders(prefix="test-folder")
    check_result("list_folders", resp)
    
    # 21. Delete folder
    resp = await ds.delete_folder(None, "test-folder/subfolder", recursive=False)
    check_result("delete_folder", resp)
    
    # ========== ACL OPERATIONS ==========
    print("\n--- ACL OPERATIONS ---")
    
    # 22. List bucket ACLs
    resp = await ds.list_bucket_access_controls()
    check_result("list_bucket_access_controls", resp)
    
    # 23. Insert bucket ACL
    entity = "user-test@example.com"
    resp = await ds.insert_bucket_access_control(entity, "READER")
    check_result("insert_bucket_access_control", resp)
    
    # 24. Get bucket ACL
    resp = await ds.get_bucket_access_control(entity)
    check_result("get_bucket_access_control", resp)

    # 25. Patch bucket ACL
    resp = await ds.patch_bucket_access_control(entity, role="OWNER")
    check_result("patch_bucket_access_control", resp)
    
    # 26. Delete bucket ACL
    resp = await ds.delete_bucket_access_control(entity)
    check_result("delete_bucket_access_control", resp)
    
    # ========== NOTIFICATIONS ==========
    print("\n--- NOTIFICATIONS ---")
    
    # 27. List notifications
    resp = await ds.list_notifications()
    check_result("list_notifications", resp)
    
    # 28. Insert notification
    topic = "projects/demo-project/topics/test-topic"
    resp = await ds.insert_notification(topic, payload_format="JSON")
    check_result("insert_notification", resp) # Will likely fail without Pub/Sub in emu

    # ========== OBJECT ACLs ==========
    print("\n--- OBJECT ACLs ---")
    
    # Setup ACL object
    test_acl_obj = "test-acl.txt"
    await ds.upload_object(None, test_acl_obj, b"data")

    # 30. List Object ACLs
    resp = await ds.list_object_access_controls(None, test_acl_obj)
    check_result("list_object_access_controls", resp)

    # 31. Insert Object ACL
    resp = await ds.insert_object_access_control(None, test_acl_obj, entity, "READER")
    check_result("insert_object_access_control", resp)

    # 32. Get Object ACL
    resp = await ds.get_object_access_control(None, test_acl_obj, entity)
    check_result("get_object_access_control", resp)
    
    # 33. Patch Object ACL
    resp = await ds.patch_object_access_control(None, test_acl_obj, entity, role="OWNER")
    check_result("patch_object_access_control", resp)
    
    # 34. Delete Object ACL
    resp = await ds.delete_object_access_control(None, test_acl_obj, entity)
    check_result("delete_object_access_control", resp)
    
    # Cleanup
    await ds.delete_object(None, test_acl_obj)
    
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
    
    # Close client
    await client.close_async_client()


async def simple_example() -> None:
    """Simple example usage showcasing core GCS operations."""
    # (Keeping your original simple_example logic here if needed, 
    # but focusing on full test suite for now)
    pass

if __name__ == "__main__":
    asyncio.run(test_all_operations())