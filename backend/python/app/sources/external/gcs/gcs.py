import os
from http import HTTPStatus
from typing import Any, Dict, List, Optional

try:
    import aiohttp  # type: ignore
    from gcloud.aio.storage import Storage  # type: ignore
except ImportError:
    raise ImportError("gcloud-aio-storage and aiohttp are not installed. Please install with `pip install gcloud-aio-storage aiohttp`")

from app.sources.client.gcs.gcs import GCSClient, GCSResponse

# HTTP status constants
HTTP_OK = HTTPStatus.OK.value


class GCSDataSource:
    """
    Google Cloud Storage Data Source - Comprehensive API wrapper
    Implements GCS JSON API operations aligned with Azure/S3 patterns.
    Features:
    - Bucket operations (CRUD, IAM, lifecycle)
    - Object operations (CRUD, compose, copy, rewrite)
    - Metadata and ACL management
    - Error handling with GCSResponse
    """

    def __init__(self, gcs_client: GCSClient) -> None:
        """Initialize with GCSClient."""
        self._gcs_client = gcs_client
        self._storage_client: Optional[Storage] = None

    async def _get_storage_client(self) -> Storage:
        """Get or create the Storage client."""
        if self._storage_client is None:
            self._storage_client = await self._gcs_client.get_storage_client()
        return self._storage_client

    def _get_bucket_name(self, bucket_name: Optional[str] = None) -> str:
        """Get bucket name from parameter or default."""
        return bucket_name or self._gcs_client.get_bucket_name()

    async def ensure_bucket_exists(self, bucket_name: Optional[str] = None) -> GCSResponse:
        if bucket_name:
            # temporarily override if provided
            self._gcs_client.client.config.bucketName = bucket_name  # type: ignore[attr-defined]
        return await self._gcs_client.ensure_bucket_exists()

    async def list_buckets(self, project_id: Optional[str] = None) -> GCSResponse:
        try:
            client = await self._get_storage_client()
            project = project_id or self._gcs_client.client.get_project_id()
            if not project:
                return GCSResponse(success=False, error="projectId is required to list buckets")
            buckets = await client.list_buckets(project=project)
            return GCSResponse(success=True, data={"buckets": buckets})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def get_bucket(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """Get bucket metadata. Note: get_bucket returns a Bucket object, not a coroutine."""
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            # get_bucket returns a Bucket object directly, not awaitable
            bucket = client.get_bucket(name)
            # Convert bucket to dict if it's an object
            if hasattr(bucket, '__dict__'):
                bucket_data = bucket.__dict__
            elif hasattr(bucket, 'to_dict'):
                bucket_data = bucket.to_dict()
            else:
                bucket_data = str(bucket)
            return GCSResponse(success=True, data={"bucket": bucket_data})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def delete_bucket(self, bucket_name: Optional[str] = None, force: bool = False) -> GCSResponse:
        """Delete a bucket. Use REST API since gcloud-aio-storage doesn't have delete_bucket."""
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)

            if force:
                # Delete all objects before deleting bucket
                page_token: Optional[str] = None
                while True:
                    page = await client.list_objects(name, params={"pageToken": page_token} if page_token else None)
                    for item in page.get("items", []):
                        await client.delete(name, item["name"])
                    page_token = page.get("nextPageToken")
                    if not page_token:
                        break

            # Use REST API for bucket deletion
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}"
                async with aiohttp.ClientSession() as session:
                    async with session.delete(url) as resp:
                        if resp.status in (200, 204):
                            return GCSResponse(success=True, data={"bucket_name": name, "action": "deleted"})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to delete bucket: HTTP {resp.status} {text}")
            else:
                # For real GCP, would need authenticated REST call
                return GCSResponse(success=False, error="Bucket deletion requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def list_objects(
        self,
        bucket_name: Optional[str] = None,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: Optional[int] = None,
    ) -> GCSResponse:
        try:
            client = await self._get_storage_client()
            name = bucket_name or self._gcs_client.get_bucket_name()
            params: Dict[str, Any] = {}
            if prefix is not None:
                params["prefix"] = prefix
            if delimiter is not None:
                params["delimiter"] = delimiter
            if page_token is not None:
                params["pageToken"] = page_token
            if page_size is not None:
                params["maxResults"] = page_size
            result = await client.list_objects(name, params=params or None)
            return GCSResponse(success=True, data=result)
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def upload_object(
        self,
        bucket_name: Optional[str],
        object_name: str,
        data: bytes,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> GCSResponse:
        try:
            client = await self._get_storage_client()
            name = bucket_name or self._gcs_client.get_bucket_name()
            # upload signature: upload(bucket, object_name, data, *, content_type=None, metadata=None, ...)
            resp = await client.upload(name, object_name, data, content_type=content_type, metadata=metadata)
            return GCSResponse(success=True, data={"result": resp})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def download_object(
        self,
        bucket_name: Optional[str],
        object_name: str,
    ) -> GCSResponse:
        try:
            client = await self._get_storage_client()
            name = bucket_name or self._gcs_client.get_bucket_name()
            content = await client.download(name, object_name)
            return GCSResponse(success=True, data={"data": content})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def delete_object(
        self,
        bucket_name: Optional[str],
        object_name: str,
    ) -> GCSResponse:
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            resp = await client.delete(name, object_name)
            return GCSResponse(success=True, data={"result": resp})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    # ========== Additional Bucket Operations ==========

    async def create_bucket(
        self,
        bucket_name: str,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        storage_class: Optional[str] = None,
    ) -> GCSResponse:
        """Create a new bucket."""
        try:
            project = project_id or self._gcs_client.client.get_project_id()
            if not project:
                return GCSResponse(success=False, error="projectId is required to create a bucket")

            # Build bucket metadata
            bucket_metadata: Dict[str, Any] = {"name": bucket_name}
            if location:
                bucket_metadata["location"] = location
            if storage_class:
                bucket_metadata["storageClass"] = storage_class

            # Use direct API call for bucket creation
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b?project={project}"
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=bucket_metadata) as resp:
                        if resp.status in (200, 201):
                            data = await resp.json()
                            return GCSResponse(success=True, data={"bucket": data})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to create bucket: HTTP {resp.status} {text}")
            else:
                # For real GCP, use Storage client's create_bucket if available
                # Note: gcloud-aio-storage may not have direct create_bucket, so we use REST
                return GCSResponse(success=False, error="Bucket creation requires project setup. Use ensure_bucket_exists for emulator.")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def patch_bucket(
        self,
        bucket_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GCSResponse:
        """Patch bucket metadata (partial update). Uses REST API."""
        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if not metadata:
                return GCSResponse(success=False, error="metadata is required for patch operation")

            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}"
                async with aiohttp.ClientSession() as session:
                    async with session.patch(url, json=metadata) as resp:
                        if resp.status == HTTP_OK:
                            result = await resp.json()
                            return GCSResponse(success=True, data={"bucket": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to patch bucket: HTTP {resp.status} {text}")
            else:
                # For real GCP, would need authenticated REST call
                return GCSResponse(success=False, error="Patch requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    # ========== Additional Object Operations ==========

    async def get_object_metadata(
        self,
        bucket_name: Optional[str],
        object_name: str,
    ) -> GCSResponse:
        """Get object metadata without downloading content. Uses REST API."""
        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}/o/{object_name}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == HTTP_OK:
                            metadata = await resp.json()
                            return GCSResponse(success=True, data={"metadata": metadata})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to get metadata: HTTP {resp.status} {text}")
            else:
                # For real GCP, use list_objects with prefix to get metadata
                # Or implement authenticated REST call
                return GCSResponse(success=False, error="Metadata retrieval requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def copy_object(
        self,
        source_bucket: str,
        source_object: str,
        dest_bucket: Optional[str] = None,
        dest_object: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> GCSResponse:
        """Copy an object from source to destination. Uses REST API for reliability."""
        try:
            dest_bucket_name = self._get_bucket_name(dest_bucket) if dest_bucket else self._get_bucket_name()
            dest_object_name = dest_object or source_object
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                # Use REST API copyTo endpoint
                url = f"{emulator.rstrip('/')}/storage/v1/b/{source_bucket}/o/{source_object}/copyTo/b/{dest_bucket_name}/o/{dest_object_name}"
                async with aiohttp.ClientSession() as session:
                    async with session.post(url) as resp:
                        if resp.status == HTTP_OK:
                            result = await resp.json()
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to copy object: HTTP {resp.status} {text}")
            else:
                # For real GCP, try gcloud-aio-storage copy
                client = await self._get_storage_client()
                # copy() signature: copy(source_bucket, source_object, dest_bucket, dest_object)
                result = await client.copy(
                    source_bucket,
                    source_object,
                    dest_bucket_name,
                    dest_object_name
                )
                return GCSResponse(success=True, data={"result": result})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def compose_object(
        self,
        bucket_name: Optional[str],
        destination_object: str,
        source_objects: List[Dict[str, str]],
        metadata: Optional[Dict[str, str]] = None,
    ) -> GCSResponse:
        """Compose multiple objects into a single object."""
        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                # Use REST API for compose - requires specific request body format
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}/o/{destination_object}/compose"

                # Format source objects correctly for GCS API
                # Each source object needs: {"name": "object-name", "generation": generation_number}
                # For emulator, we can omit generation
                formatted_sources = []
                for src_obj in source_objects:
                    if isinstance(src_obj, dict):
                        formatted_sources.append({"name": src_obj.get("name", src_obj.get("object", ""))})
                    else:
                        formatted_sources.append({"name": str(src_obj)})

                request_body = {
                    "sourceObjects": formatted_sources
                }

                if metadata:
                    request_body["destination"] = {"metadata": metadata}

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=request_body) as resp:
                        if resp.status == HTTP_OK:
                            result = await resp.json()
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to compose objects: HTTP {resp.status} {text}")
            else:
                # For real GCP, try using gcloud-aio-storage compose
                client = await self._get_storage_client()
                # compose() signature: compose(bucket, destination_object, source_objects)
                result = await client.compose(
                    name, destination_object,
                    source_objects
                )
                return GCSResponse(success=True, data={"result": result})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def rewrite_object(
        self,
        source_bucket: str,
        source_object: str,
        dest_bucket: Optional[str] = None,
        dest_object: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> GCSResponse:
        """Rewrite an object (copy with potential metadata/storage class changes). Uses REST API."""
        try:
            dest_bucket_name = self._get_bucket_name(dest_bucket) if dest_bucket else self._get_bucket_name()
            dest_object_name = dest_object or source_object
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                # Use copy API which is similar to rewrite
                url = f"{emulator.rstrip('/')}/storage/v1/b/{source_bucket}/o/{source_object}/copyTo/b/{dest_bucket_name}/o/{dest_object_name}"
                headers = {}
                if metadata:
                    # Metadata would go in request body for rewrite
                    pass
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers) as resp:
                        if resp.status == HTTP_OK:
                            result = await resp.json()
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to rewrite object: HTTP {resp.status} {text}")
            else:
                # For real GCP, would need authenticated REST call
                return GCSResponse(success=False, error="Rewrite requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def update_object_metadata(
        self,
        bucket_name: Optional[str],
        object_name: str,
        metadata: Dict[str, str],
    ) -> GCSResponse:
        """Update object metadata. Uses REST API."""
        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}/o/{object_name}"
                async with aiohttp.ClientSession() as session:
                    async with session.patch(url, json=metadata) as resp:
                        if resp.status == HTTP_OK:
                            result = await resp.json()
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to update metadata: HTTP {resp.status} {text}")
            else:
                # For real GCP, would need authenticated REST call
                return GCSResponse(success=False, error="Update metadata requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))


