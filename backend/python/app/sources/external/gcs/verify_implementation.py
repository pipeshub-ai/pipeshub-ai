"""
Quick verification script to check which gcloud-aio-storage methods are available.
"""
import asyncio

try:
    from gcloud.aio.storage import Storage  # type: ignore
except ImportError:
    print("❌ gcloud-aio-storage not installed")
    exit(1)

async def check_methods() -> None:
    """Check available methods in Storage class."""
    print("Checking gcloud-aio-storage Storage class methods...")
    print("=" * 80)

    storage = Storage()

    # Check common methods
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

if __name__ == "__main__":
    asyncio.run(check_methods())

