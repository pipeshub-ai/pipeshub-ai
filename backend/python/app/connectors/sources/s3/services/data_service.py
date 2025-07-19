"""Data service for S3 connector"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aioboto3
from botocore.exceptions import ClientError

from app.connectors.core.base.data_service.data_service import BaseDataService
from app.connectors.core.interfaces.auth.iauth_service import IAuthenticationService


@dataclass
class S3Object:
    """Data class for S3 object information"""
    key: str
    size: int
    last_modified: str
    etag: str
    storage_class: str
    bucket_name: str


@dataclass
class S3Bucket:
    """Data class for S3 bucket information"""
    name: str
    creation_date: str
    region: str


class S3DataService(BaseDataService):
    """Handles data operations for AWS S3 API"""

    def __init__(self, logger: logging.Logger, auth_service: IAuthenticationService) -> None:
        super().__init__(logger, auth_service)
        self.auth_service = auth_service

    def _get_session(self) -> Optional[aioboto3.Session]:
        """Get the current session from auth service"""
        return self.auth_service.get_service()

    async def list_items(self, path: str = "/", recursive: bool = True) -> List[Dict[str, Any]]:
        """List buckets from S3 using aioboto3 (path is ignored for S3)"""
        try:
            self.logger.info("📦 Listing S3 buckets using aioboto3")

            session = self._get_session()
            if not session:
                self.logger.error("❌ No active AWS session")
                return []

            # Use aioboto3 session to create S3 client
            async with session.client('s3') as s3_client:
                try:
                    response = await s3_client.list_buckets()
                    buckets = []
                    for bucket in response.get('Buckets', []):
                        bucket_info = {
                            "id": bucket['Name'],
                            "name": bucket['Name'],
                            "creation_date": bucket['CreationDate'].isoformat(),
                            "type": "bucket",
                            "size": 0,  # Buckets don't have a size
                            "last_modified": bucket['CreationDate'].isoformat()
                        }
                        buckets.append(bucket_info)

                    self.logger.info(f"✅ Found {len(buckets)} S3 buckets using aioboto3")
                    return buckets

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    self.logger.error(f"❌ Failed to list S3 buckets: {error_code}")
                    return []

        except Exception as e:
            self.logger.error(f"❌ Failed to list S3 buckets: {str(e)}")
            return []

    async def get_item_metadata(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific S3 bucket using aioboto3"""
        try:
            self.logger.info(f"📋 Getting metadata for bucket: {item_id} using aioboto3")

            session = self._get_session()
            if not session:
                self.logger.error("❌ No active AWS session")
                return None

            # Use aioboto3 session to create S3 client
            async with session.client('s3') as s3_client:
                try:
                    # Get bucket location
                    location_response = await s3_client.get_bucket_location(Bucket=item_id)

                    # Get bucket versioning
                    try:
                        versioning_response = await s3_client.get_bucket_versioning(Bucket=item_id)
                    except ClientError:
                        versioning_response = {}

                    # Get bucket encryption
                    try:
                        encryption_response = await s3_client.get_bucket_encryption(Bucket=item_id)
                    except ClientError:
                        encryption_response = {}

                    # Get bucket policy
                    try:
                        policy_response = await s3_client.get_bucket_policy(Bucket=item_id)
                    except ClientError:
                        policy_response = {}

                    metadata = {
                        "id": item_id,
                        "name": item_id,
                        "type": "bucket",
                        "region": location_response.get('LocationConstraint') or 'us-east-1',
                        "versioning": versioning_response.get('Status', 'Disabled'),
                        "mfa_delete": versioning_response.get('MfaDelete', 'Disabled'),
                        "encryption": encryption_response.get('ServerSideEncryptionConfiguration'),
                        "has_policy": bool(policy_response.get('Policy')),
                    }

                    self.logger.info(f"✅ Retrieved metadata for bucket: {item_id} using aioboto3")
                    return metadata

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == 'NoSuchBucket':
                        self.logger.error(f"❌ Bucket {item_id} does not exist")
                    elif error_code == 'AccessDenied':
                        self.logger.error(f"❌ Access denied to bucket {item_id}")
                    else:
                        self.logger.error(f"❌ Failed to get bucket metadata: {error_code}")
                    return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get S3 bucket metadata: {str(e)}")
            return None

    async def get_item_content(self, item_id: str) -> Optional[bytes]:
        """Get content for a specific S3 object using aioboto3 (bucket:key format)"""
        try:
            self.logger.info(f"📝 Getting content for S3 object: {item_id} using aioboto3")

            session = self._get_session()
            if not session:
                self.logger.error("❌ No active AWS session")
                return None

            # Parse bucket and key from item_id
            if ':' not in item_id:
                self.logger.error("❌ Invalid S3 object ID format. Expected 'bucket:key'")
                return None

            bucket_name, object_key = item_id.split(':', 1)

            # Use aioboto3 session to create S3 client
            async with session.client('s3') as s3_client:
                try:
                    response = await s3_client.get_object(Bucket=bucket_name, Key=object_key)
                    content = await response['Body'].read()

                    self.logger.info(f"✅ Retrieved content for S3 object: {item_id} ({len(content)} bytes) using aioboto3")
                    return content

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == 'NoSuchKey':
                        self.logger.error(f"❌ S3 object {item_id} does not exist")
                    elif error_code == 'AccessDenied':
                        self.logger.error(f"❌ Access denied to S3 object {item_id}")
                    else:
                        self.logger.error(f"❌ Failed to get S3 object content: {error_code}")
                    return None

        except Exception as e:
            self.logger.error(f"❌ Failed to get S3 object content: {str(e)}")
            return None

    async def search_items(self, query: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Search for S3 objects using aioboto3 (limited search capability)"""
        try:
            self.logger.info(f"🔍 Searching S3 objects with query: {query} using aioboto3")

            session = self._get_session()
            if not session:
                self.logger.error("❌ No active AWS session")
                return []

            # S3 doesn't have native search, so we'll list objects and filter
            bucket_name = filters.get('bucket_name') if filters else None
            if not bucket_name:
                self.logger.error("❌ Bucket name is required for S3 search")
                return []

            # Use aioboto3 session to create S3 client
            async with session.client('s3') as s3_client:
                try:
                    # List objects with prefix matching query
                    response = await s3_client.list_objects_v2(
                        Bucket=bucket_name,
                        Prefix=query,
                        MaxKeys=1000
                    )

                    objects = []
                    for obj in response.get('Contents', []):
                        object_info = {
                            "id": f"{bucket_name}:{obj['Key']}",
                            "name": obj['Key'],
                            "type": "object",
                            "size": obj['Size'],
                            "last_modified": obj['LastModified'].isoformat(),
                            "etag": obj['ETag'].strip('"'),
                            "storage_class": obj.get('StorageClass', 'STANDARD'),
                            "bucket_name": bucket_name
                        }
                        objects.append(object_info)

                    self.logger.info(f"✅ Found {len(objects)} S3 objects matching query using aioboto3")
                    return objects

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    self.logger.error(f"❌ Failed to search S3 objects: {error_code}")
                    return []

        except Exception as e:
            self.logger.error(f"❌ Failed to search S3 objects: {str(e)}")
            return []

    async def get_item_permissions(self, item_id: str) -> List[Dict[str, Any]]:
        """Get permissions for a specific S3 bucket/object"""
        try:
            self.logger.info(f"🔐 Getting permissions for S3 item: {item_id}")

            session = self._get_session()
            if not session:
                self.logger.error("❌ No active AWS session")
                return []

            # S3 permissions are managed through IAM policies, not directly accessible via API
            # This would need to be implemented using AWS IAM API calls
            self.logger.warning("⚠️ S3 permissions API not fully implemented")
            return []

        except Exception as e:
            self.logger.error(f"❌ Failed to get S3 item permissions: {str(e)}")
            return []

    async def list_bucket_objects(self, bucket_name: str, prefix: str = '', max_keys: int = 1000) -> List[Dict[str, Any]]:
        """List objects in a specific S3 bucket using aioboto3"""
        try:
            self.logger.info(f"📦 Listing objects in S3 bucket: {bucket_name} using aioboto3")

            session = self._get_session()
            if not session:
                self.logger.error("❌ No active AWS session")
                return []

            # Use aioboto3 session to create S3 client
            async with session.client('s3') as s3_client:
                try:
                    params = {
                        'Bucket': bucket_name,
                        'MaxKeys': max_keys
                    }

                    if prefix:
                        params['Prefix'] = prefix

                    response = await s3_client.list_objects_v2(**params)

                    objects = []
                    for obj in response.get('Contents', []):
                        object_info = {
                            "id": f"{bucket_name}:{obj['Key']}",
                            "name": obj['Key'],
                            "type": "object",
                            "size": obj['Size'],
                            "last_modified": obj['LastModified'].isoformat(),
                            "etag": obj['ETag'].strip('"'),
                            "storage_class": obj.get('StorageClass', 'STANDARD'),
                            "bucket_name": bucket_name
                        }
                        objects.append(object_info)

                    self.logger.info(f"✅ Found {len(objects)} objects in bucket {bucket_name} using aioboto3")
                    return objects

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == 'NoSuchBucket':
                        self.logger.error(f"❌ Bucket {bucket_name} does not exist")
                    elif error_code == 'AccessDenied':
                        self.logger.error(f"❌ Access denied to bucket {bucket_name}")
                    else:
                        self.logger.error(f"❌ Failed to list objects in bucket {bucket_name}: {error_code}")
                    return []

        except Exception as e:
            self.logger.error(f"❌ Failed to list S3 bucket objects: {str(e)}")
            return []
