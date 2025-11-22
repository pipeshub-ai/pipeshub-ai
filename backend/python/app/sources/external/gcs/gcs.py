import logging
from typing import Any, Dict, List, Optional

try:
    from google.cloud import storage  # type: ignore
    from google.cloud.exceptions import NotFound  # type: ignore
except ImportError:
    raise ImportError("google-cloud-storage is not installed. Please install with `pip install google-cloud-storage`")

from app.sources.client.gcs.gcs import GCSClient, GCSResponse

# Set up logger
logger = logging.getLogger(__name__)


class GCSDataSource:
    """
    Google Cloud Storage Data Source - API wrapper using official SDK.
    Implements GCS operations aligned with Azure/S3 patterns.

    Features:
    - Uses official google-cloud-storage SDK
    - Handles both Production and Emulator environments transparently
    """

    def __init__(self, gcs_client: GCSClient) -> None:
        """Initialize with GCSClient."""
        self._gcs_client = gcs_client
        self._storage_client: Optional[storage.Client] = None

    async def _get_storage_client(self) -> storage.Client:
        """Get or create the Storage client."""
        if self._storage_client is None:
            self._storage_client = await self._gcs_client.get_storage_client()
        return self._storage_client

    def _get_bucket_name(self, bucket_name: Optional[str] = None) -> str:
        """Get bucket name from parameter or default."""
        return bucket_name or self._gcs_client.get_bucket_name()

    async def compose_object(self, bucket_name: str, destination_object: str, source_objects: List[str], metadata: Optional[Dict[str, Any]] = None) -> GCSResponse:
        """compose_object operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            destination = bucket.blob(destination_object)
            sources = [bucket.blob(s) for s in source_objects]
            destination.compose(sources)
            return GCSResponse(success=True, data={"composed": destination_object})
        except Exception as e:
            logger.error(f"Error in compose_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def copy_object(self, source_bucket: str, source_object: str, dest_bucket: Optional[str] = None, dest_object: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> GCSResponse:
        """copy_object operation."""
        try:
            client = await self._get_storage_client()
            s_bucket = client.bucket(source_bucket)
            s_blob = s_bucket.blob(source_object)
            d_bucket_name = self._get_bucket_name(dest_bucket)
            d_bucket = client.bucket(d_bucket_name)
            d_object_name = dest_object or source_object
            s_bucket.copy_blob(s_blob, d_bucket, d_object_name)
            return GCSResponse(success=True, data={"source": source_object, "destination": d_object_name})
        except Exception as e:
            logger.error(f"Error in copy/rewrite: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def create_bucket(self, bucket_name: str, project_id: Optional[str] = None, location: Optional[str] = None, storage_class: Optional[str] = None) -> GCSResponse:
        """create_bucket operation."""
        try:
            client = await self._get_storage_client()
            bucket = client.bucket(bucket_name)
            if location:
                bucket.location = location
            if storage_class:
                bucket.storage_class = storage_class
            bucket.create(project=project_id)
            return GCSResponse(success=True, data={"bucket": bucket.name, "action": "created"})
        except Exception as e:
            logger.error(f"Error in create_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def create_folder(self, bucket_name: str, folder_path: str, metadata: Optional[Dict[str, Any]] = None) -> GCSResponse:
        """create_folder operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            folder_name = folder_path if folder_path.endswith("/") else f"{folder_path}/"
            bucket = client.bucket(name)
            blob = bucket.blob(folder_name)
            blob.upload_from_string(b"", content_type="application/x-directory")
            return GCSResponse(success=True, data={"folder": folder_name, "action": "created"})
        except Exception as e:
            logger.error(f"Error in create_folder: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def delete_bucket(self, bucket_name: Optional[str] = None, force: bool = False) -> GCSResponse:
        """delete_bucket operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            if force:
                blobs = list(client.list_blobs(name))
                for blob in blobs:
                    blob.delete()
            bucket.delete(force=force)
            return GCSResponse(success=True, data={"bucket": name, "action": "deleted"})
        except Exception as e:
            logger.error(f"Error in delete_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def delete_bucket_access_control(self, entity: str, bucket_name: Optional[str] = None) -> GCSResponse:
        """delete_bucket_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            bucket.acl.reload()
            bucket.acl.revoke(entity)
            bucket.acl.save()
            return GCSResponse(success=True, data={"action": "acl_deleted"})
        except Exception as e:
            logger.error(f"Error in delete_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def delete_folder(self, bucket_name: str, folder_path: str, recursive: bool = False) -> GCSResponse:
        """delete_folder operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            prefix = folder_path if folder_path.endswith("/") else f"{folder_path}/"
            if recursive:
                blobs = list(client.list_blobs(name, prefix=prefix))
                for blob in blobs:
                    blob.delete()
            else:
                try:
                    bucket = client.bucket(name)
                    blob = bucket.blob(prefix)
                    blob.delete()
                except NotFound:
                    return GCSResponse(success=True, data={"folder": prefix, "message": "Folder not found"})
            return GCSResponse(success=True, data={"folder": prefix, "action": "deleted"})
        except Exception as e:
            logger.error(f"Error in delete_folder: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def delete_notification(self, notification_id: str, bucket_name: Optional[str] = None) -> GCSResponse:
        """delete_notification operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            notif = bucket.get_notification(notification_id)
            notif.delete()
            return GCSResponse(success=True, data={"action": "notification_deleted"})
        except Exception as e:
            logger.error(f"Error in delete_notification: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def delete_object(self, bucket_name: str, object_name: str) -> GCSResponse:
        """delete_object operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.delete()
            return GCSResponse(success=True, data={"bucket": name, "object": object_name, "action": "deleted"})
        except Exception as e:
            logger.error(f"Error in delete_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def delete_object_access_control(self, bucket_name: str, object_name: str, entity: str) -> GCSResponse:
        """delete_object_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.acl.reload()
            blob.acl.revoke(entity)
            blob.acl.save()
            return GCSResponse(success=True, data={"action": "acl_deleted"})
        except Exception as e:
            logger.error(f"Error in delete_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def download_object(self, bucket_name: str, object_name: str) -> GCSResponse:
        """download_object operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            content = blob.download_as_bytes()
            return GCSResponse(success=True, data={"data": content})
        except Exception as e:
            logger.error(f"Error in download_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def ensure_bucket_exists(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """ensure_bucket_exists operation."""
        if bucket_name:
            self._gcs_client.client.config.bucketName = bucket_name
        return await self._gcs_client.ensure_bucket_exists()

    async def get_bucket(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """get_bucket operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            if not bucket.exists():
                return GCSResponse(success=False, error=f"Bucket {name} not found")
            bucket.reload()
            return GCSResponse(success=True, data={"bucket": bucket.name, "location": bucket.location})
        except Exception as e:
            logger.error(f"Error in get_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def get_bucket_access_control(self, entity: str, bucket_name: Optional[str] = None) -> GCSResponse:
        """get_bucket_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            # Manually iterate to find entity
            acls = list(bucket.acl)
            found = [str(a) for a in acls if entity in str(a)]
            if found:
                 return GCSResponse(success=True, data={"acl": found[0]})
            return GCSResponse(success=False, error="ACL entity not found")
        except Exception as e:
            logger.error(f"Error in get_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def get_bucket_iam_policy(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """get_bucket_iam_policy operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            policy = bucket.get_iam_policy()
            return GCSResponse(success=True, data={"policy": policy.to_api_repr()})
        except Exception as e:
            logger.error(f"Error in get_bucket_iam_policy: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def get_notification(self, notification_id: str, bucket_name: Optional[str] = None) -> GCSResponse:
        """get_notification operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            notif = bucket.get_notification(notification_id)
            return GCSResponse(success=True, data={"notification": str(notif)})
        except Exception as e:
            logger.error(f"Error in get_notification: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def get_object_access_control(self, bucket_name: str, object_name: str, entity: str) -> GCSResponse:
        """get_object_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            acls = list(blob.acl)
            found = [str(a) for a in acls if entity in str(a)]
            if found:
                 return GCSResponse(success=True, data={"acl": found[0]})
            return GCSResponse(success=False, error="ACL entity not found")
        except Exception as e:
            logger.error(f"Error in get_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def get_object_metadata(self, bucket_name: str, object_name: str) -> GCSResponse:
        """get_object_metadata operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.get_blob(object_name)
            if not blob:
                return GCSResponse(success=False, error=f"Object {object_name} not found")
            return GCSResponse(success=True, data={"metadata": blob.metadata, "content_type": blob.content_type, "size": blob.size})
        except Exception as e:
            logger.error(f"Error in get_object_metadata: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def insert_bucket_access_control(self, entity: str, role: str, bucket_name: Optional[str] = None) -> GCSResponse:
        """insert_bucket_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            bucket.acl.entity_from_dict({"entity": entity, "role": role})
            bucket.acl.save()
            return GCSResponse(success=True, data={"action": "acl_inserted"})
        except Exception as e:
            logger.error(f"Error in insert_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def insert_notification(self, topic: str, bucket_name: Optional[str] = None, payload_format: Optional[str] = None, event_types: Optional[List[str]] = None, object_name_prefix: Optional[str] = None) -> GCSResponse:
        """insert_notification operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            notif = bucket.notification(topic_name=topic, custom_attributes=None, payload_format=payload_format)
            notif.create()
            return GCSResponse(success=True, data={"action": "notification_created"})
        except Exception as e:
            logger.error(f"Error in insert_notification: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def insert_object_access_control(self, bucket_name: str, object_name: str, entity: str, role: str) -> GCSResponse:
        """insert_object_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.acl.entity_from_dict({"entity": entity, "role": role})
            blob.acl.save()
            return GCSResponse(success=True, data={"action": "acl_inserted"})
        except Exception as e:
            logger.error(f"Error in insert_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def list_bucket_access_controls(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """list_bucket_access_controls operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            acls = list(bucket.acl)
            return GCSResponse(success=True, data={"items": [str(acl) for acl in acls]})
        except Exception as e:
            logger.error(f"Error in list_bucket_access_controls: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def list_buckets(self, project_id: Optional[str] = None) -> GCSResponse:
        """list_buckets operation."""
        try:
            client = await self._get_storage_client()
            buckets = list(client.list_buckets())
            bucket_names = [b.name for b in buckets]
            return GCSResponse(success=True, data={"buckets": bucket_names})
        except Exception as e:
            logger.error(f"Error in list_buckets: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def list_folders(self, bucket_name: Optional[str] = None, prefix: Optional[str] = None, delimiter: Optional[str] = None) -> GCSResponse:
        """list_folders operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            blobs = client.list_blobs(name, prefix=prefix, delimiter=delimiter or '/')
            # Note: .prefixes is populated only AFTER iterating blobs
            _ = [blob.name for blob in blobs]
            return GCSResponse(success=True, data={"folders": list(blobs.prefixes)})
        except Exception as e:
            logger.error(f"Error in list_folders: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def list_notifications(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """list_notifications operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            notifs = list(bucket.list_notifications())
            return GCSResponse(success=True, data={"items": [str(n) for n in notifs]})
        except Exception as e:
            logger.error(f"Error in list_notifications: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def list_object_access_controls(self, bucket_name: str, object_name: str) -> GCSResponse:
        """list_object_access_controls operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            acls = list(blob.acl)
            return GCSResponse(success=True, data={"items": [str(acl) for acl in acls]})
        except Exception as e:
            logger.error(f"Error in list_object_access_controls: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def list_objects(self, bucket_name: Optional[str] = None, prefix: Optional[str] = None, delimiter: Optional[str] = None, page_token: Optional[str] = None, page_size: Optional[int] = None) -> GCSResponse:
        """list_objects operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            blobs = client.list_blobs(name, prefix=prefix, delimiter=delimiter)
            items = [blob.name for blob in blobs]
            return GCSResponse(success=True, data={"items": items, "prefixes": list(blobs.prefixes)})
        except Exception as e:
            logger.error(f"Error in list_objects: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def patch_bucket(self, bucket_name: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> GCSResponse:
        """patch_bucket operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            if metadata:
                for key, value in metadata.items():
                    setattr(bucket, key, value)
                bucket.patch()
            return GCSResponse(success=True, data={"bucket": name, "action": "patched"})
        except Exception as e:
            logger.error(f"Error in patch_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def patch_bucket_access_control(self, entity: str, bucket_name: Optional[str] = None, role: Optional[str] = None) -> GCSResponse:
        """patch_bucket_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            bucket.acl.entity_from_dict({"entity": entity, "role": role})
            bucket.acl.save()
            return GCSResponse(success=True, data={"action": "acl_patched"})
        except Exception as e:
            logger.error(f"Error in patch_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def patch_object_access_control(self, bucket_name: str, object_name: str, entity: str, role: Optional[str] = None) -> GCSResponse:
        """patch_object_access_control operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.acl.entity_from_dict({"entity": entity, "role": role})
            blob.acl.save()
            return GCSResponse(success=True, data={"action": "acl_patched"})
        except Exception as e:
            logger.error(f"Error in patch_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def rewrite_object(self, source_bucket: str, source_object: str, dest_bucket: Optional[str] = None, dest_object: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> GCSResponse:
        """rewrite_object operation."""
        try:
            client = await self._get_storage_client()
            s_bucket = client.bucket(source_bucket)
            s_blob = s_bucket.blob(source_object)
            d_bucket_name = self._get_bucket_name(dest_bucket)
            d_bucket = client.bucket(d_bucket_name)
            d_object_name = dest_object or source_object
            s_bucket.copy_blob(s_blob, d_bucket, d_object_name)
            return GCSResponse(success=True, data={"source": source_object, "destination": d_object_name})
        except Exception as e:
            logger.error(f"Error in copy/rewrite: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def set_bucket_iam_policy(self, policy: Dict[str, Any], bucket_name: Optional[str] = None) -> GCSResponse:
        """set_bucket_iam_policy operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            from google.api_core.iam import Policy
            new_policy = Policy.from_api_repr(policy)
            bucket.set_iam_policy(new_policy)
            return GCSResponse(success=True, data={"action": "iam_policy_set"})
        except Exception as e:
            logger.error(f"Error in set_bucket_iam_policy: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def test_bucket_iam_permissions(self, permissions: List[str], bucket_name: Optional[str] = None) -> GCSResponse:
        """test_bucket_iam_permissions operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            permissions_allowed = bucket.test_iam_permissions(permissions)
            return GCSResponse(success=True, data={"permissions": permissions_allowed})
        except Exception as e:
            logger.error(f"Error in test_bucket_iam_permissions: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def update_object_metadata(self, bucket_name: str, object_name: str, metadata: Dict[str, str]) -> GCSResponse:
        """update_object_metadata operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.get_blob(object_name)
            if blob:
                blob.metadata = metadata
                blob.patch()
                return GCSResponse(success=True, data={"object": object_name, "action": "metadata_updated"})
            return GCSResponse(success=False, error="Object not found")
        except Exception as e:
            logger.error(f"Error in update_object_metadata: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

    async def upload_object(self, bucket_name: str, object_name: str, data: bytes, content_type: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> GCSResponse:
        """upload_object operation."""

        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.upload_from_string(data, content_type=content_type)
            if metadata:
                blob.metadata = metadata
                blob.patch()
            return GCSResponse(success=True, data={"bucket": name, "object": object_name, "action": "uploaded"})
        except Exception as e:
            logger.error(f"Error in upload_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))

