from typing import Any, Dict, Optional

try:
    from gcloud.aio.storage import Storage  # type: ignore
except ImportError:
    raise ImportError("gcloud-aio-storage is not installed. Please install it with `pip install gcloud-aio-storage`")

from app.sources.client.gcs.gcs import GCSClient, GCSResponse


class GCSDataSource:
    def __init__(self, gcs_client: GCSClient) -> None:
        self._gcs_client = gcs_client
        self._storage_client: Optional[Storage] = None

    async def _get_storage_client(self) -> Storage:
        if self._storage_client is None:
            self._storage_client = await self._gcs_client.get_storage_client()
        return self._storage_client

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
        try:
            client = await self._get_storage_client()
            name = bucket_name or self._gcs_client.get_bucket_name()
            bucket = await client.get_bucket(name)
            return GCSResponse(success=True, data={"bucket": bucket})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))

    async def delete_bucket(self, bucket_name: Optional[str] = None, force: bool = False) -> GCSResponse:
        try:
            client = await self._get_storage_client()
            name = bucket_name or self._gcs_client.get_bucket_name()
            if force:
                # Delete all objects before deleting bucket
                page_token: Optional[str] = None
                while True:
                    page = await client.list_objects(name, params={"pageToken": page_token} if page_token else None)
                    for item in page.get("items", []):
                        await client.delete(name, item["name"])  # bucket, object
                    page_token = page.get("nextPageToken")
                    if not page_token:
                        break
            await client.delete_bucket(name)
            return GCSResponse(success=True, data={"bucket_name": name, "action": "deleted"})
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
            name = bucket_name or self._gcs_client.get_bucket_name()
            resp = await client.delete(name, object_name)
            return GCSResponse(success=True, data={"result": resp})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))


