# ruff: noqa
"""
S3 API example demonstrating:
1. List Buckets
2. List Objects
2a. List Objects with Timestamp Filter
3. Get Object (direct download with binary data and mimetype)
4. Get Object ACL
5. Download via Presigned URL
"""
import asyncio
import os
import mimetypes
import inspect
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import httpx  # type: ignore

from app.sources.client.s3.s3 import S3Client, S3AccessKeyConfig, S3Response
from app.sources.external.s3.s3 import S3DataSource


async def list_buckets(s3_data_source: S3DataSource) -> List[Dict[str, Any]]:
    """
    List all S3 buckets.
    
    Args:
        s3_data_source: S3DataSource instance
        
    Returns:
        List of bucket dictionaries with Name and CreationDate
    """
    print("\n" + "="*60)
    print("1. LIST BUCKETS")
    print("="*60)
    
    response: S3Response = await s3_data_source.list_buckets()
    
    if not response.success:
        print(f"Error listing buckets: {response.error}")
        return []
    
    buckets_data = response.data
    if not buckets_data or 'Buckets' not in buckets_data:
        print("No buckets found")
        return []
    
    buckets = buckets_data['Buckets']
    print(f"Found {len(buckets)} bucket(s):")
    
    for bucket in buckets:
        bucket_name = bucket.get('Name', 'Unknown')
        creation_date = bucket.get('CreationDate', 'Unknown')
        print(f"  - {bucket_name} (Created: {creation_date})")
    
    return buckets


async def list_objects(
    s3_data_source: S3DataSource,
    bucket_name: str,
    prefix: Optional[str] = None,
    max_keys: int = 100
) -> List[Dict[str, Any]]:
    """
    List objects in an S3 bucket.
    
    Args:
        s3_data_source: S3DataSource instance
        bucket_name: Name of the S3 bucket
        prefix: Optional prefix to filter objects
        max_keys: Maximum number of keys to return
        
    Returns:
        List of object dictionaries
    """
    print("\n" + "="*60)
    print(f"2. LIST OBJECTS (Bucket: {bucket_name})")
    print("="*60)
    
    response: S3Response = await s3_data_source.list_objects_v2(
        Bucket=bucket_name,
        Prefix=prefix,
        MaxKeys=max_keys
    )
    
    if not response.success:
        print(f"Error listing objects: {response.error}")
        return []
    
    objects_data = response.data
    objects = []
    
    if objects_data and 'Contents' in objects_data:
        objects = objects_data['Contents']
        print(f"Found {len(objects)} object(s):")
        
        for obj in objects:
            key = obj.get('Key', 'Unknown')
            size = obj.get('Size', 0)
            last_modified = obj.get('LastModified', 'Unknown')
            storage_class = obj.get('StorageClass', 'STANDARD')
            print(f"  - {key}")
            print(f"    Size: {size} bytes, Modified: {last_modified}, Storage: {storage_class}")
    else:
        print("No objects found in bucket")
    
    return objects


async def list_objects_with_timestamp(
    s3_data_source: S3DataSource,
    bucket_name: str,
    timestamp: str,
    prefix: Optional[str] = None,
    max_keys: int = 100
) -> List[Dict[str, Any]]:
    """
    List objects in an S3 bucket filtered by timestamp.
    Only returns objects modified after the specified timestamp.
    
    Args:
        s3_data_source: S3DataSource instance
        bucket_name: Name of the S3 bucket
        timestamp: ISO format timestamp (e.g., '2024-01-01T00:00:00Z'). 
                   Only objects modified after this timestamp will be returned.
        prefix: Optional prefix to filter objects
        max_keys: Maximum number of keys to return
        
    Returns:
        List of object dictionaries modified after the timestamp
    """
    print("\n" + "="*60)
    print(f"2a. LIST OBJECTS WITH TIMESTAMP FILTER (Bucket: {bucket_name})")
    print(f"    Timestamp: {timestamp}")
    print("="*60)
    
    response: S3Response = await s3_data_source.list_objects_v2(
        Bucket=bucket_name,
        Prefix=prefix,
        MaxKeys=max_keys
    )
    
    if not response.success:
        print(f"Error listing objects: {response.error}")
        return []
    
    objects_data = response.data
    objects = []
    
    if objects_data and 'Contents' in objects_data:
        # Parse the timestamp for comparison
        try:
            timestamp_clean = timestamp.replace('Z', '+00:00') if timestamp.endswith('Z') else timestamp
            filter_timestamp = datetime.fromisoformat(timestamp_clean)
            
            # Filter objects by LastModified
            for obj in objects_data['Contents']:
                last_modified_str = obj.get('LastModified')
                if last_modified_str:
                    try:
                        if isinstance(last_modified_str, str):
                            last_modified_clean = last_modified_str.replace('Z', '+00:00') if last_modified_str.endswith('Z') else last_modified_str
                            last_modified = datetime.fromisoformat(last_modified_clean)
                        else:
                            last_modified = last_modified_str
                        
                        # Only include objects modified after the timestamp
                        if last_modified > filter_timestamp:
                            objects.append(obj)
                    except (ValueError, AttributeError):
                        continue
            
            print(f"Found {len(objects)} object(s) modified after {timestamp}:")
            
            for obj in objects:
                key = obj.get('Key', 'Unknown')
                size = obj.get('Size', 0)
                last_modified = obj.get('LastModified', 'Unknown')
                storage_class = obj.get('StorageClass', 'STANDARD')
                print(f"  - {key}")
                print(f"    Size: {size} bytes, Modified: {last_modified}, Storage: {storage_class}")
        except ValueError as e:
            print(f"Error parsing timestamp: {e}")
            return []
    else:
        print("No objects found in bucket")
    
    return objects


async def get_object_direct(
    s3_data_source: S3DataSource,
    bucket_name: str,
    key: str
) -> Optional[Dict[str, Any]]:
    """
    Get object directly via S3 API, fetching binary data and metadata.
    
    Args:
        s3_data_source: S3DataSource instance
        bucket_name: Name of the S3 bucket
        key: Object key (path)
        
    Returns:
        Dictionary with content (bytes), metadata, and mimetype, or None on error
    """
    print("\n" + "="*60)
    print(f"3. GET OBJECT (Direct Download)")
    print(f"   Bucket: {bucket_name}, Key: {key}")
    print("="*60)
    
    response: S3Response = await s3_data_source.get_object(
        Bucket=bucket_name,
        Key=key
    )
    
    if not response.success:
        print(f"Error fetching object: {response.error}")
        return None
    
    response_data = response.data
    if not response_data:
        print("No data in response")
        return None
    
    # Extract metadata
    content_type = response_data.get('ContentType', 'application/octet-stream')
    content_length = response_data.get('ContentLength', 0)
    last_modified = response_data.get('LastModified', 'Unknown')
    etag = response_data.get('ETag', 'Unknown')
    metadata = response_data.get('Metadata', {})
    
    print(f"Content-Type (MIME): {content_type}")
    print(f"Content-Length: {content_length} bytes")
    print(f"Last-Modified: {last_modified}")
    print(f"ETag: {etag}")
    if metadata:
        print(f"Metadata: {metadata}")
    
    # Get binary data from Body
    body = response_data.get('Body')
    if not body:
        print("No body in response")
        return None
    
    try:
        # Read the body content
        # With aioboto3, StreamingBody.read() is typically sync but blocks
        # Some versions might have async read(), so we handle both
        
        # First, call read() to see what we get
        read_result = body.read()
        
        # Check if the result is a coroutine that needs awaiting
        if inspect.iscoroutine(read_result):
            # It's async - await it
            binary_data = await read_result
        elif hasattr(read_result, '__await__'):
            # It's awaitable - await it
            binary_data = await read_result
        else:
            # It's sync - the result is already bytes, but if we called read() directly
            # in async context, it might block. For safety, if it's not bytes yet,
            # it means read() returned something else (unlikely)
            if isinstance(read_result, bytes):
                binary_data = read_result
            else:
                # This shouldn't happen, but just in case, try reading in thread
                binary_data = await asyncio.to_thread(body.read)
        
        # Ensure we have bytes
        if not isinstance(binary_data, bytes):
            if isinstance(binary_data, bytearray):
                binary_data = bytes(binary_data)
            elif isinstance(binary_data, memoryview):
                binary_data = bytes(binary_data)
            else:
                print(f"Unexpected data type: {type(binary_data).__name__}")
                # Try to convert if possible
                try:
                    binary_data = bytes(binary_data)
                except (TypeError, ValueError) as e:
                    print(f"Could not convert to bytes: {e}")
                    return None
        
        print(f"\n✓ Successfully downloaded {len(binary_data)} bytes of binary data")
        
        # Try to determine file type from extension if ContentType is generic
        detected_mimetype = None
        if content_type == 'application/octet-stream' or not content_type:
            detected_mimetype, _ = mimetypes.guess_type(key)
            if detected_mimetype:
                print(f"Detected MIME type from extension: {detected_mimetype}")
        
        # Show preview for text files
        if content_type and content_type.startswith('text/'):
            try:
                preview = binary_data[:200].decode('utf-8', errors='ignore')
                print(f"\nText preview (first 200 chars):\n{preview}")
            except Exception as e:
                print(f"Could not decode as text: {e}")
        elif content_type and ('json' in content_type or 'xml' in content_type):
            try:
                preview = binary_data[:200].decode('utf-8', errors='ignore')
                print(f"\nContent preview (first 200 chars):\n{preview}")
            except Exception as e:
                print(f"Could not decode as text: {e}")
        else:
            print(f"Binary file - showing first 20 bytes in hex: {binary_data[:20].hex()}")
        
        return {
            'content': binary_data,
            'content_type': content_type or detected_mimetype or 'application/octet-stream',
            'content_length': len(binary_data),
            'last_modified': last_modified,
            'etag': etag,
            'metadata': metadata
        }
    except Exception as e:
        print(f"Error reading binary data: {e}")
        return None
    finally:
        # Close the body stream
        if hasattr(body, 'close'):
            try:
                body.close()
            except Exception:
                pass


async def get_object_acl(
    s3_data_source: S3DataSource,
    bucket_name: str,
    key: str
) -> Optional[Dict[str, Any]]:
    """
    Get object ACL (Access Control List).
    
    Args:
        s3_data_source: S3DataSource instance
        bucket_name: Name of the S3 bucket
        key: Object key (path)
        
    Returns:
        Dictionary with ACL information, or None on error
    """
    print("\n" + "="*60)
    print(f"4. GET OBJECT ACL")
    print(f"   Bucket: {bucket_name}, Key: {key}")
    print("="*60)
    
    response: S3Response = await s3_data_source.get_object_acl(
        Bucket=bucket_name,
        Key=key
    )
    
    if not response.success:
        print(f"Error fetching object ACL: {response.error}")
        return None
    
    acl_data = response.data
    if not acl_data:
        print("No ACL data in response")
        return None
    
    owner = acl_data.get('Owner', {})
    grants = acl_data.get('Grants', [])
    
    print(f"Owner: {owner.get('DisplayName', 'Unknown')} (ID: {owner.get('ID', 'Unknown')})")
    print(f"Grants ({len(grants)}):")
    
    for grant in grants:
        grantee = grant.get('Grantee', {})
        permission = grant.get('Permission', 'Unknown')
        grantee_type = grantee.get('Type', 'Unknown')
        grantee_id = grantee.get('ID', grantee.get('DisplayName', 'Unknown'))
        print(f"  - {grantee_type}: {grantee_id} -> {permission}")
    
    return acl_data


async def download_via_presigned_url(
    s3_data_source: S3DataSource,
    bucket_name: str,
    key: str,
    expires_in: int = 3600
) -> Optional[bytes]:
    """
    Download object using a presigned URL.
    
    Args:
        s3_data_source: S3DataSource instance
        bucket_name: Name of the S3 bucket
        key: Object key (path)
        expires_in: URL expiration time in seconds (default: 1 hour)
        
    Returns:
        Binary data of the object, or None on error
    """
    print("\n" + "="*60)
    print(f"5. DOWNLOAD VIA PRESIGNED URL")
    print(f"   Bucket: {bucket_name}, Key: {key}")
    print(f"   Expires in: {expires_in} seconds")
    print("="*60)
    
    # Generate presigned URL
    response: S3Response = await s3_data_source.generate_presigned_url(
        ClientMethod='get_object',
        Params={
            'Bucket': bucket_name,
            'Key': key
        },
        ExpiresIn=expires_in
    )
    
    if not response.success:
        print(f"Error generating presigned URL: {response.error}")
        return None
    
    presigned_url = response.data
    if not presigned_url:
        print("No presigned URL in response")
        return None
    
    print(f"Generated presigned URL: {presigned_url[:100]}...")
    
    # Download using the presigned URL
    try:
        async with httpx.AsyncClient() as client:
            http_response = await client.get(presigned_url)
            http_response.raise_for_status()
            
            binary_data = http_response.content
            content_type = http_response.headers.get('content-type', 'application/octet-stream')
            content_length = len(binary_data)
            
            print(f"\n✓ Successfully downloaded via presigned URL")
            print(f"Content-Type: {content_type}")
            print(f"Content-Length: {content_length} bytes")
            
            # Show preview for text files
            if content_type.startswith('text/') or 'json' in content_type or 'xml' in content_type:
                try:
                    preview = binary_data[:200].decode('utf-8', errors='ignore')
                    print(f"\nContent preview (first 200 chars):\n{preview}")
                except Exception:
                    pass
            else:
                print(f"Binary file - showing first 20 bytes in hex: {binary_data[:20].hex()}")
            
            return binary_data
    except Exception as e:
        print(f"Error downloading from presigned URL: {e}")
        return None


async def main():
    """Main function demonstrating all S3 operations."""
    # S3 credentials from environment
    ACCESS_KEY = os.getenv("S3_ACCESS_KEY_ID")
    SECRET_KEY = os.getenv("S3_SECRET_ACCESS")
    REGION = os.getenv("S3_REGION", "us-east-1")
    BUCKET = os.getenv("S3_BUCKET_NAME")
    
    if not ACCESS_KEY or not SECRET_KEY:
        print("Error: S3_ACCESS_KEY_ID and S3_SECRET_ACCESS must be set in environment")
        return
    
    # Create S3 client and data source
    config = S3AccessKeyConfig(
        access_key_id=ACCESS_KEY,
        secret_access_key=SECRET_KEY,
        region_name=REGION,
        bucket_name=BUCKET
    )
    
    client = S3Client.build_with_config(config)
    s3_data_source = S3DataSource(client)
    
    print("\n" + "="*60)
    print("S3 API EXAMPLE - Demonstrating all operations")
    print("="*60)
    
    # 1. List buckets
    buckets = await list_buckets(s3_data_source)
    
    if not buckets:
        print("\nNo buckets found. Exiting.")
        return
    
    # Determine which bucket(s) to try
    buckets_to_try = []
    if BUCKET:
        # Use specified bucket first
        buckets_to_try.append(BUCKET)
    else:
        # Try all available buckets
        buckets_to_try = [b.get('Name') for b in buckets if b.get('Name')]
    
    if not buckets_to_try:
        print("\nNo bucket name available. Exiting.")
        return
    
    # 2. Try to list objects in buckets until we find one we can access
    target_bucket = None
    objects = []
    access_errors = []
    
    for bucket_name in buckets_to_try:
        if not bucket_name:
            continue
        print(f"\nTrying bucket: {bucket_name}")
        objects = await list_objects(s3_data_source, bucket_name, max_keys=10)
        
        if objects:
            target_bucket = bucket_name
            print(f"\n✓ Successfully accessed bucket: {target_bucket}")
            
            # 2a. Example: List objects with timestamp filter
            # Get objects modified in the last 30 days
            thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            await list_objects_with_timestamp(
                s3_data_source, 
                target_bucket, 
                timestamp=thirty_days_ago,
                max_keys=10
            )
            break
        else:
            # Check if it was an access error by trying to get the last response
            # We'll note it and continue
            access_errors.append(bucket_name)
            print(f"  ⚠ Could not access or no objects in bucket: {bucket_name}")
            if len(buckets_to_try) > 1:
                print("  Trying next bucket...")
    
    if not target_bucket or not objects:
        print("\n" + "="*60)
        print("⚠ WARNING: Could not access any buckets or no objects found.")
        print("="*60)
        if access_errors:
            print(f"Failed to access {len(access_errors)} bucket(s).")
            print("This usually means:")
            print("  - Missing s3:ListBucket permission")
            print("  - Bucket is empty")
            print("  - Bucket doesn't exist or is in a different region")
        print("\nTip: Set S3_BUCKET_NAME environment variable to specify a bucket you have access to.")
        return
    
    # Use first object for demonstration
    first_object = objects[0]
    object_key = first_object.get('Key')
    
    if not object_key:
        print("\nNo object key found. Exiting.")
        return
    
    # 3. Get object directly (with binary data and mimetype)
    object_data = await get_object_direct(s3_data_source, target_bucket, object_key)
    
    # 4. Get object ACL
    acl_data = await get_object_acl(s3_data_source, target_bucket, object_key)
    
    # 5. Download via presigned URL
    presigned_data = await download_via_presigned_url(s3_data_source, target_bucket, object_key)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"✓ Listed {len(buckets)} bucket(s)")
    print(f"✓ Listed {len(objects)} object(s) in bucket '{target_bucket}'")
    if object_data:
        print(f"✓ Downloaded object '{object_key}' directly ({object_data['content_length']} bytes, {object_data['content_type']})")
    if acl_data:
        print(f"✓ Retrieved ACL for object '{object_key}'")
    if presigned_data:
        print(f"✓ Downloaded object '{object_key}' via presigned URL ({len(presigned_data)} bytes)")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())