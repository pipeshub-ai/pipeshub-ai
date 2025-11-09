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

    Uses:
    - gcloud-aio-storage: Third-party async library for GCS operations
    - google-auth: Official Google authentication library
    - REST API: For operations not available in gcloud-aio-storage
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

    async def compose_object(
        self,
        bucket_name: str,
        destination_object: str,
        source_objects: List[Dict[str, str]],
        metadata: Optional[Dict[str, Any]] = None
    ) -> GCSResponse:
        """Compose multiple objects into a single object.

        Args:
            bucket_name: Required parameter
            destination_object: Required parameter
            source_objects: Required parameter
            metadata: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}/o/{destination_object}/compose"
                
                # Format source objects correctly for GCS API
                formatted_sources = []
                for src_obj in source_objects:
                    if isinstance(src_obj, dict):
                        formatted_sources.append({"name": src_obj.get("name", src_obj.get("object", ""))})
                    else:
                        formatted_sources.append({"name": str(src_obj)})
                
                request_body = {"sourceObjects": formatted_sources}
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

    async def copy_object(
        self,
        source_bucket: str,
        source_object: str,
        dest_bucket: Optional[str] = None,
        dest_object: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> GCSResponse:
        """Copy an object from source to destination.

        Args:
            source_bucket: Required parameter
            source_object: Required parameter
            dest_bucket: Optional parameter
            dest_object: Optional parameter
            metadata: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
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

    async def create_bucket(
        self,
        bucket_name: str,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        storage_class: Optional[str] = None
    ) -> GCSResponse:
        """Create a new bucket.

        Args:
            bucket_name: Required parameter
            project_id: Optional parameter
            location: Optional parameter
            storage_class: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            project = project_id or self._gcs_client.client.get_project_id()
            if not project:
                            return GCSResponse(success=False, error="projectId is required to create a bucket")
            bucket_metadata: Dict[str, Any] = {"name": bucket_name}
            if location:
                            bucket_metadata["location"] = location
            if storage_class:
                            bucket_metadata["storageClass"] = storage_class
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b?project={project}"
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=bucket_metadata) as resp:
                        if resp.status in (200, 201) if isinstance((200, 201), tuple) else resp.status == (200, 201):
                            try:
                                result = await resp.json()
                            except Exception:
                                # Handle empty/null response
                                result = {"status": "success"}
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed: HTTP {resp.status} {text}")
            else:
                return GCSResponse(success=False, error="create_bucket requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def delete_bucket(self, bucket_name: Optional[str] = None, force: bool = False) -> GCSResponse:
        """Delete a bucket.

        Args:
            bucket_name: Optional parameter
            force: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}"
                async with aiohttp.ClientSession() as session:
                    async with session.delete(url) as resp:
                        if resp.status in (200, 204) if isinstance((200, 204), tuple) else resp.status == (200, 204):
                            try:
                                result = await resp.json()
                            except Exception:
                                # Handle empty/null response
                                result = {"status": "success"}
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed: HTTP {resp.status} {text}")
            else:
                return GCSResponse(success=False, error="delete_bucket requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def delete_object(self, bucket_name: str, object_name: str) -> GCSResponse:
        """Delete an object from a bucket.

        Args:
            bucket_name: Required parameter
            object_name: Required parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            result = await client.delete(name, object_name)
            return GCSResponse(success=True, data={"result": result} if not isinstance(result, dict) else result)
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def download_object(self, bucket_name: str, object_name: str) -> GCSResponse:
        """Download an object from a bucket.

        Args:
            bucket_name: Required parameter
            object_name: Required parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            content = await client.download(name, object_name)
            return GCSResponse(success=True, data={"data": content})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def ensure_bucket_exists(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """Ensure bucket exists, create if needed.

        Args:
            bucket_name: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        if bucket_name:
            # temporarily override if provided
            self._gcs_client.client.config.bucketName = bucket_name  # type: ignore[attr-defined]
        return await self._gcs_client.ensure_bucket_exists()

    async def get_bucket(self, bucket_name: Optional[str] = None) -> GCSResponse:
        """Get bucket metadata.

        Args:
            bucket_name: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
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

    async def get_object_metadata(self, bucket_name: str, object_name: str) -> GCSResponse:
        """Get object metadata without downloading content.

        Args:
            bucket_name: Required parameter
            object_name: Required parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}/o/{object_name}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status in HTTP_OK if isinstance(HTTP_OK, tuple) else resp.status == HTTP_OK:
                            try:
                                result = await resp.json()
                            except Exception:
                                # Handle empty/null response
                                result = {"status": "success"}
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed: HTTP {resp.status} {text}")
            else:
                return GCSResponse(success=False, error="get_object_metadata requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def list_buckets(self, project_id: Optional[str] = None) -> GCSResponse:
        """List all buckets in a project.

        Args:
            project_id: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            client = await self._get_storage_client()
            project = project_id or self._gcs_client.client.get_project_id()
            if not project:
                            return GCSResponse(success=False, error="projectId is required")
            result = await client.list_buckets(project=project)
            return GCSResponse(success=True, data={"result": result} if not isinstance(result, dict) else result)
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def list_objects(
        self,
        bucket_name: Optional[str] = None,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: Optional[int] = None
    ) -> GCSResponse:
        """List objects in a bucket.

        Args:
            bucket_name: Optional parameter
            prefix: Optional parameter
            delimiter: Optional parameter
            page_token: Optional parameter
            page_size: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
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
            return GCSResponse(success=True, data={"result": result} if not isinstance(result, dict) else result)
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def patch_bucket(self, bucket_name: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> GCSResponse:
        """Patch bucket metadata (partial update).

        Args:
            bucket_name: Optional parameter
            metadata: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            name = self._get_bucket_name(bucket_name)
            if not metadata:
                            return GCSResponse(success=False, error="metadata is required for patch_bucket operation")
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}"
                async with aiohttp.ClientSession() as session:
                    async with session.patch(url, json=metadata) as resp:
                        if resp.status in HTTP_OK if isinstance(HTTP_OK, tuple) else resp.status == HTTP_OK:
                            try:
                                result = await resp.json()
                            except Exception:
                                # Handle empty/null response
                                result = {"status": "success"}
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed: HTTP {resp.status} {text}")
            else:
                return GCSResponse(success=False, error="patch_bucket requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def rewrite_object(
        self,
        source_bucket: str,
        source_object: str,
        dest_bucket: Optional[str] = None,
        dest_object: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> GCSResponse:
        """Rewrite an object (copy with potential metadata/storage class changes).

        Args:
            source_bucket: Required parameter
            source_object: Required parameter
            dest_bucket: Optional parameter
            dest_object: Optional parameter
            metadata: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            dest_bucket_name = self._get_bucket_name(dest_bucket) if dest_bucket else self._get_bucket_name()
            dest_object_name = dest_object or source_object
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{source_bucket}/o/{source_object}/copyTo/b/{dest_bucket_name}/o/{dest_object_name}"
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers={}) as resp:
                        if resp.status in HTTP_OK if isinstance(HTTP_OK, tuple) else resp.status == HTTP_OK:
                            try:
                                result = await resp.json()
                            except Exception:
                                # Handle empty/null response
                                result = {"status": "success"}
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed: HTTP {resp.status} {text}")
            else:
                return GCSResponse(success=False, error="rewrite_object requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def update_object_metadata(self, bucket_name: str, object_name: str, metadata: Dict[str, str]) -> GCSResponse:
        """Update object metadata.

        Args:
            bucket_name: Required parameter
            object_name: Required parameter
            metadata: Required parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            name = self._get_bucket_name(bucket_name)
            if not metadata:
                            return GCSResponse(success=False, error="metadata is required for update_object_metadata operation")
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            
            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}/o/{object_name}"
                async with aiohttp.ClientSession() as session:
                    async with session.patch(url, json=metadata) as resp:
                        if resp.status in HTTP_OK if isinstance(HTTP_OK, tuple) else resp.status == HTTP_OK:
                            try:
                                result = await resp.json()
                            except Exception:
                                # Handle empty/null response
                                result = {"status": "success"}
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed: HTTP {resp.status} {text}")
            else:
                return GCSResponse(success=False, error="update_object_metadata requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def upload_object(
        self,
        bucket_name: str,
        object_name: str,
        data: bytes,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> GCSResponse:
        """Upload an object to a bucket.

        Args:
            bucket_name: Required parameter
            object_name: Required parameter
            data: Required parameter
            content_type: Optional parameter
            metadata: Optional parameter

        Returns:
            GCSResponse: Standardized response with success/data/error format        """
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            result = await client.upload(name, object_name, data, content_type=content_type, metadata=metadata)
            return GCSResponse(success=True, data={"result": result} if not isinstance(result, dict) else result)
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

