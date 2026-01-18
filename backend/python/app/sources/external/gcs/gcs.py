"""
Google Cloud Storage Data Source

Provides a data source class for interacting with Google Cloud Storage API.
"""

from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

try:
    from google.cloud import storage  # type: ignore
    from google.cloud.storage import Blob, Bucket  # type: ignore
    from google.api_core.exceptions import GoogleAPIError, NotFound  # type: ignore
except ImportError:
    raise ImportError(
        "google-cloud-storage is not installed. Please install it with "
        "`pip install google-cloud-storage`"
    )

from app.sources.client.gcs.gcs import GCSClient, GCSResponse


class GCSDataSource:
    """
    Google Cloud Storage API client wrapper.

    This class provides methods for interacting with GCS buckets and objects
    using the google-cloud-storage library.
    """

    def __init__(self, gcs_client: GCSClient) -> None:
        """Initialize with GCSClient."""
        self._gcs_client = gcs_client
        self._storage_client: Optional[storage.Client] = None

    def _get_storage_client(self) -> storage.Client:
        """Get or create the storage client."""
        if self._storage_client is None:
            self._storage_client = self._gcs_client.get_storage_client()
        return self._storage_client

    def _handle_response(
        self,
        data: Optional[Any] = None,
        error: Optional[str] = None
    ) -> GCSResponse:
        """Create a standardized response."""
        if error:
            return GCSResponse(success=False, error=error)
        return GCSResponse(success=True, data=data)

    async def list_buckets(self) -> GCSResponse:
        """List all buckets in the project.

        Returns:
            GCSResponse with list of bucket information
        """
        try:
            client = self._get_storage_client()
            buckets = list(client.list_buckets())

            bucket_list = []
            for bucket in buckets:
                bucket_info = {
                    "name": bucket.name,
                    "id": bucket.id,
                    "location": bucket.location,
                    "location_type": bucket.location_type,
                    "storage_class": bucket.storage_class,
                    "time_created": bucket.time_created.isoformat() if bucket.time_created else None,
                    "updated": bucket.updated.isoformat() if bucket.updated else None,
                    "project_number": bucket.project_number,
                }
                bucket_list.append(bucket_info)

            return self._handle_response(data={"Buckets": bucket_list})

        except GoogleAPIError as e:
            return self._handle_response(error=f"GCS API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def list_blobs(
        self,
        bucket_name: str,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None,
        max_results: Optional[int] = None,
        page_token: Optional[str] = None,
        versions: bool = False
    ) -> GCSResponse:
        """List blobs (objects) in a bucket.

        Args:
            bucket_name: Name of the bucket
            prefix: Filter blobs by prefix
            delimiter: Delimiter for hierarchy (usually '/')
            max_results: Maximum number of results per page
            page_token: Token for pagination
            versions: If True, list all versions of blobs

        Returns:
            GCSResponse with list of blob information and pagination info
        """
        try:
            client = self._get_storage_client()
            bucket = client.bucket(bucket_name)

            # Build list parameters
            kwargs: Dict[str, Any] = {}
            if prefix is not None:
                kwargs["prefix"] = prefix
            if delimiter is not None:
                kwargs["delimiter"] = delimiter
            if max_results is not None:
                kwargs["max_results"] = max_results
            if page_token is not None:
                kwargs["page_token"] = page_token
            if versions:
                kwargs["versions"] = versions

            # Get blob iterator
            blob_iterator = client.list_blobs(bucket_name, **kwargs)

            # Get the current page
            page = blob_iterator._next_page()

            blobs_list = []
            prefixes = []

            if page:
                for blob in page:
                    blob_info = self._blob_to_dict(blob)
                    blobs_list.append(blob_info)

                # Get common prefixes (folder-like paths when using delimiter)
                if hasattr(blob_iterator, "prefixes") and blob_iterator.prefixes:
                    prefixes = list(blob_iterator.prefixes)

            # Get next page token
            next_page_token = blob_iterator.next_page_token

            return self._handle_response(data={
                "Contents": blobs_list,
                "CommonPrefixes": [{"Prefix": p} for p in prefixes],
                "IsTruncated": next_page_token is not None,
                "NextContinuationToken": next_page_token,
                "KeyCount": len(blobs_list),
            })

        except NotFound:
            return self._handle_response(error=f"Bucket not found: {bucket_name}")
        except GoogleAPIError as e:
            return self._handle_response(error=f"GCS API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def list_blobs_with_pagination(
        self,
        bucket_name: str,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None,
        page_size: int = 1000
    ) -> AsyncIterator[Dict[str, Any]]:
        """Iterate through all blobs in a bucket with automatic pagination.

        Args:
            bucket_name: Name of the bucket
            prefix: Filter blobs by prefix
            delimiter: Delimiter for hierarchy
            page_size: Number of results per page

        Yields:
            Dict containing blob information
        """
        page_token = None

        while True:
            response = await self.list_blobs(
                bucket_name=bucket_name,
                prefix=prefix,
                delimiter=delimiter,
                max_results=page_size,
                page_token=page_token
            )

            if not response.success:
                raise Exception(response.error)

            data = response.data or {}
            contents = data.get("Contents", [])

            for blob_info in contents:
                yield blob_info

            # Check for more pages
            if not data.get("IsTruncated"):
                break

            page_token = data.get("NextContinuationToken")
            if not page_token:
                break

    def _blob_to_dict(self, blob: Blob) -> Dict[str, Any]:
        """Convert a Blob object to a dictionary.

        Args:
            blob: GCS Blob object

        Returns:
            Dictionary with blob information
        """
        return {
            "Key": blob.name,
            "Size": blob.size,
            "LastModified": blob.updated.isoformat() if blob.updated else None,
            "ETag": blob.etag,
            "ContentType": blob.content_type,
            "StorageClass": blob.storage_class,
            "TimeCreated": blob.time_created.isoformat() if blob.time_created else None,
            "Md5Hash": blob.md5_hash,
            "Crc32c": blob.crc32c,
            "Generation": blob.generation,
            "Metageneration": blob.metageneration,
            "Metadata": blob.metadata,
        }

    async def get_blob(
        self,
        bucket_name: str,
        blob_name: str,
        generation: Optional[int] = None
    ) -> GCSResponse:
        """Get a blob's content.

        Args:
            bucket_name: Name of the bucket
            blob_name: Name of the blob (object key)
            generation: Specific generation/version to retrieve

        Returns:
            GCSResponse with blob content and metadata
        """
        try:
            client = self._get_storage_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name, generation=generation)

            # Download content
            content = blob.download_as_bytes()

            return self._handle_response(data={
                "Body": content,
                "ContentLength": blob.size,
                "ContentType": blob.content_type,
                "ETag": blob.etag,
                "LastModified": blob.updated.isoformat() if blob.updated else None,
                "Metadata": blob.metadata,
            })

        except NotFound:
            return self._handle_response(
                error=f"Blob not found: {bucket_name}/{blob_name}"
            )
        except GoogleAPIError as e:
            return self._handle_response(error=f"GCS API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def head_blob(
        self,
        bucket_name: str,
        blob_name: str,
        generation: Optional[int] = None
    ) -> GCSResponse:
        """Get blob metadata without downloading content.

        Args:
            bucket_name: Name of the bucket
            blob_name: Name of the blob (object key)
            generation: Specific generation/version

        Returns:
            GCSResponse with blob metadata
        """
        try:
            client = self._get_storage_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name, generation=generation)

            # Reload to fetch metadata from server
            blob.reload()

            return self._handle_response(data={
                "ContentLength": blob.size,
                "ContentType": blob.content_type,
                "ETag": blob.etag,
                "LastModified": blob.updated.isoformat() if blob.updated else None,
                "TimeCreated": blob.time_created.isoformat() if blob.time_created else None,
                "StorageClass": blob.storage_class,
                "Md5Hash": blob.md5_hash,
                "Crc32c": blob.crc32c,
                "Generation": blob.generation,
                "Metageneration": blob.metageneration,
                "Metadata": blob.metadata,
            })

        except NotFound:
            return self._handle_response(
                error=f"Blob not found: {bucket_name}/{blob_name}"
            )
        except GoogleAPIError as e:
            return self._handle_response(error=f"GCS API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def generate_signed_url(
        self,
        bucket_name: str,
        blob_name: str,
        expiration: int = 3600,
        method: str = "GET",
        content_type: Optional[str] = None,
        response_disposition: Optional[str] = None
    ) -> GCSResponse:
        """Generate a signed URL for accessing a blob.

        Args:
            bucket_name: Name of the bucket
            blob_name: Name of the blob (object key)
            expiration: URL expiration time in seconds (default 1 hour)
            method: HTTP method (GET, PUT, etc.)
            content_type: Content type for PUT requests
            response_disposition: Content-Disposition header for response

        Returns:
            GCSResponse with the signed URL
        """
        try:
            client = self._get_storage_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)

            # Build signed URL parameters
            kwargs: Dict[str, Any] = {
                "expiration": timedelta(seconds=expiration),
                "method": method,
                "version": "v4",
            }

            if content_type:
                kwargs["content_type"] = content_type

            if response_disposition:
                kwargs["response_disposition"] = response_disposition

            url = blob.generate_signed_url(**kwargs)

            return self._handle_response(data=url)

        except NotFound:
            return self._handle_response(
                error=f"Blob not found: {bucket_name}/{blob_name}"
            )
        except GoogleAPIError as e:
            return self._handle_response(error=f"GCS API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def download_blob_to_file(
        self,
        bucket_name: str,
        blob_name: str,
        destination_file: str,
        generation: Optional[int] = None
    ) -> GCSResponse:
        """Download a blob to a local file.

        Args:
            bucket_name: Name of the bucket
            blob_name: Name of the blob (object key)
            destination_file: Local file path to save to
            generation: Specific generation/version

        Returns:
            GCSResponse indicating success or failure
        """
        try:
            client = self._get_storage_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name, generation=generation)

            blob.download_to_filename(destination_file)

            return self._handle_response(data={
                "downloaded": True,
                "destination": destination_file,
                "size": blob.size,
            })

        except NotFound:
            return self._handle_response(
                error=f"Blob not found: {bucket_name}/{blob_name}"
            )
        except GoogleAPIError as e:
            return self._handle_response(error=f"GCS API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def get_bucket_location(
        self,
        bucket_name: str
    ) -> GCSResponse:
        """Get the location of a bucket.

        Args:
            bucket_name: Name of the bucket

        Returns:
            GCSResponse with bucket location information
        """
        try:
            client = self._get_storage_client()
            bucket = client.get_bucket(bucket_name)

            return self._handle_response(data={
                "LocationConstraint": bucket.location,
                "LocationType": bucket.location_type,
            })

        except NotFound:
            return self._handle_response(error=f"Bucket not found: {bucket_name}")
        except GoogleAPIError as e:
            return self._handle_response(error=f"GCS API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    def get_gcs_client(self) -> GCSClient:
        """Get the GCSClient wrapper."""
        return self._gcs_client

    def get_storage_client(self) -> storage.Client:
        """Get the underlying storage client."""
        return self._get_storage_client()
