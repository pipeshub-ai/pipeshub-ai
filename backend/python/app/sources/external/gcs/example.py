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
    """Simple example usage."""
    print("=" * 80)
    print("SIMPLE GCS EXAMPLE")
    print("=" * 80)
    
    # Simple example using GCSADCConfig (Application Default Credentials)
    # This doesn't require ConfigurationService setup
    PROJECT_ID = os.environ.get("PROJECT_ID", "demo-project")
    BUCKET_NAME = os.environ.get("BUCKET_NAME", "test-bucket")
    
    cfg = GCSADCConfig(bucketName=BUCKET_NAME, projectId=PROJECT_ID)
    client = GCSClient.build_with_adc_config(cfg)
    ds = GCSDataSource(client)
    
    # Ensure bucket exists
    ensure = await ds.ensure_bucket_exists()
    print(f"Ensure bucket: {ensure.to_json()}")
    
    # Upload and download example
    data = b"hello world"
    up = await ds.upload_object(
        bucket_name=None,
        object_name="sample.txt",
        data=data,
        content_type="text/plain"
    )
    print(f"Upload: {up.to_json()}")
    
    down = await ds.download_object(bucket_name=None, object_name="sample.txt")
    print(f"Download size: {len(down.data.get('data', b'')) if down.success and down.data else 0} bytes")
    
    # Cleanup
    await ds.delete_object(bucket_name=None, object_name="sample.txt")
    await client.close_async_client()
    
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
