from ctypes import Union
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from azure.storage.blob import (  # type: ignore
        AccountSasPermissions,
        BlobSasPermissions,
        ContainerSasPermissions,
        ResourceTypes,
        UserDelegationKey,
        generate_account_sas,
        generate_blob_sas,
        generate_container_sas,
    )
    from azure.storage.blob.aio import (  # type: ignore
        BlobServiceClient as AsyncBlobServiceClient,
    )
except ImportError:
    raise ImportError("azure-storage-blob is not installed. Please install it with `pip install azure-storage-blob`")

from app.sources.client.azure.azure_blob import AzureBlobClient, AzureBlobResponse


class AzureBlobDataSource:
    """
    Azure Blob Storage Data Source - Generated from OpenAPI Specification
    Generated from 69 operations from Azure Blob Storage API 2025-07-05
    Features:
    - All operations from OpenAPI spec
    - Correct parameter mapping with container_name/blob_name where needed
    - Proper client routing (Service -> Container -> Blob)
    - Parameters on separate lines for readability
    - Headers parameter on all methods
    - Error handling with AzureBlobResponse
    """

    def __init__(self, azure_blob_client: AzureBlobClient) -> None:
        """Initialize with AzureBlobClient."""
        self._azure_blob_client_wrapper = azure_blob_client
        self._async_blob_service_client: Optional[AsyncBlobServiceClient] = None

    async def _get_async_blob_service_client(self) -> AsyncBlobServiceClient:
        """Get the async blob service client, creating it if needed."""
        if self._async_blob_service_client is None:
            self._async_blob_service_client = await self._azure_blob_client_wrapper.get_async_blob_service_client()
        return self._async_blob_service_client

    def _build_headers(self, custom_headers: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, str]:
        """Build headers from parameters according to OpenAPI spec."""
        headers = {}

        # Add custom headers first
        if custom_headers:
            headers.update(custom_headers)

        # Map common header parameters from OpenAPI spec
        header_mappings = {
            "client_request_id": "x-ms-client-request-id",
            "lease_id": "x-ms-lease-id",
            "if_match": "If-Match",
            "if_none_match": "If-None-Match",
            "if_modified_since": "If-Modified-Since",
            "if_unmodified_since": "If-Unmodified-Since",
            "if_tags": "x-ms-if-tags",
        }

        for param_name, header_name in header_mappings.items():
            if param_name in kwargs and kwargs[param_name] is not None:
                headers[header_name] = str(kwargs[param_name])

        return headers

    async def set_service_properties(
        self,
        StorageServiceProperties: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /?restype=service&comp=properties
        Sets properties for a storage account's Blob service endpoint, including properties for Storage Analytics and CORS (Cross-Origin Resource Sharing) rules
        """
        try:
            client = await self._get_async_blob_service_client()
            request_headers = self._build_headers(headers, **kwargs)
            result = await client.set_service_properties(
                StorageServiceProperties=StorageServiceProperties,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_service_properties(
        self,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /?restype=service&comp=properties
        gets the properties of a storage account's Blob service, including properties for Storage Analytics and CORS (Cross-Origin Resource Sharing) rules.
        """
        try:
            client = await self._get_async_blob_service_client()
            request_headers = self._build_headers(headers, **kwargs)
            result = await client.get_service_properties(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_service_stats(
        self,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /?restype=service&comp=stats
        Retrieves statistics related to replication for the Blob service. It is only available on the secondary location endpoint when read-access geo-redundant replication is enabled for the storage account.
        """
        try:
            client = await self._get_async_blob_service_client()
            request_headers = self._build_headers(headers, **kwargs)
            result = await client.get_service_stats(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def list_containers(
        self,
        prefix: Optional[str] = None,
        marker: Optional[str] = None,
        maxresults: Optional[int] = None,
        include: Optional[List[str]] = None,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /?comp=list
        The List Containers Segment operation returns a list of the containers under the specified account
        """
        try:
            client = await self._get_async_blob_service_client()
            # Filter out explicitly defined parameters from kwargs to avoid conflicts
            filtered_kwargs = {k: v for k, v in kwargs.items()
                            if k not in ['prefix', 'marker', 'maxresults', 'include', 'timeout', 'client_request_id', 'headers']}
            request_headers = self._build_headers(headers, **filtered_kwargs)
            result = client.list_containers(
                name_starts_with=prefix,
                include_metadata=bool(include and 'metadata' in include),
                headers=request_headers
            )

            # Convert AsyncItemPaged to list
            containers = []
            async for container in result:
                containers.append(container)

            return AzureBlobResponse(success=True, data=containers)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_user_delegation_key(
        self,
        KeyInfo: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        POST /?restype=service&comp=userdelegationkey
        Retrieves a user delegation key for the Blob service. This is only a valid operation when using bearer token authentication.
        """
        try:
            client = await self._get_async_blob_service_client()
            request_headers = self._build_headers(headers, **kwargs)
            result = await client.get_user_delegation_key(
                KeyInfo=KeyInfo,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_account_information(
        self,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /?restype=account&comp=properties
        Returns the sku name and account kind
        """
        try:
            client = await self._get_async_blob_service_client()
            request_headers = self._build_headers(headers, **kwargs)
            result = await client.get_account_information(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def submit_batch(
        self,
        body: str,
        Content_Length: int,
        Content_Type: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        POST /?comp=batch
        The Batch operation allows multiple API calls to be embedded into a single HTTP request.
        """
        try:
            client = await self._get_async_blob_service_client()
            request_headers = self._build_headers(headers, **kwargs)
            result = await client.submit_batch(
                body=body,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def find_blobs_by_tags(
        self,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        where: Optional[str] = None,
        marker: Optional[str] = None,
        maxresults: Optional[int] = None,
        include: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /?comp=blobs
        The Filter Blobs operation enables callers to list blobs across all containers whose tags match a given search expression.  Filter blobs searches across all containers within a storage account but can be scoped within the expression to a single container.
        """
        try:
            client = await self._get_async_blob_service_client()
            request_headers = self._build_headers(headers, **kwargs)
            result = await client.find_blobs_by_tags(
                where=where,
                marker=marker,
                maxresults=maxresults,
                include=include,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def create_container(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        meta: Optional[str] = None,
        blob_public_access: Optional[str] = None,
        client_request_id: Optional[str] = None,
        default_encryption_scope: Optional[str] = None,
        deny_encryption_scope_override: Optional[bool] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?restype=container
        creates a new container under the specified account. If the container with the same name already exists, the operation fails
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.create_container(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_container_properties(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}?restype=container
        returns all user-defined metadata and system properties for the specified container. The data returned does not include the container's list of blobs
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.get_container_properties(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def delete_container(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        DELETE /{containerName}?restype=container
        operation marks the specified container for deletion. The container and any blobs contained within it are later deleted during garbage collection
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.delete_container(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_container_metadata(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        meta: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?restype=container&comp=metadata
        operation sets one or more user-defined name-value pairs for the specified container.
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.set_container_metadata(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_container_access_policy(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}?restype=container&comp=acl
        gets the permissions for the specified container. The permissions indicate whether container data may be accessed publicly.
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.get_container_access_policy(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_container_access_policy(
        self,
        container_name: str,
        containerAcl: Optional[str] = None,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        blob_public_access: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?restype=container&comp=acl
        sets the permissions for the specified container. The permissions indicate whether blobs in a container may be accessed publicly.
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.set_container_access_policy(
                containerAcl=containerAcl,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def restore_container(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        deleted_container_name: Optional[str] = None,
        deleted_container_version: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?restype=container&comp=undelete
        Restores a previously-deleted container.
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.restore_container(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def rename_container(
        self,
        container_name: str,
        source_container_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        source_lease_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?restype=container&comp=rename
        Renames an existing container.
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.rename_container(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def submit_container_batch(
        self,
        container_name: str,
        body: str,
        Content_Length: int,
        Content_Type: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        POST /{containerName}?restype=container&comp=batch
        The Batch operation allows multiple API calls to be embedded into a single HTTP request.
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.submit_container_batch(
                body=body,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def filter_blobs_in_container(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        where: Optional[str] = None,
        marker: Optional[str] = None,
        maxresults: Optional[int] = None,
        include: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}?restype=container&comp=blobs
        The Filter Blobs operation enables callers to list blobs in a container whose tags match a given search expression.  Filter blobs searches within the given container.
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.filter_blobs_in_container(
                where=where,
                marker=marker,
                maxresults=maxresults,
                include=include,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def acquire_container_lease(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        lease_duration: Optional[int] = None,
        proposed_lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?comp=lease&restype=container&acquire
        [Update] establishes and manages a lock on a container for delete operations. The lock duration can be 15 to 60 seconds, or can be infinite
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.acquire_container_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def release_container_lease(
        self,
        container_name: str,
        lease_id: str,
        timeout: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?comp=lease&restype=container&release
        [Update] establishes and manages a lock on a container for delete operations. The lock duration can be 15 to 60 seconds, or can be infinite
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.release_container_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def renew_container_lease(
        self,
        container_name: str,
        lease_id: str,
        timeout: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?comp=lease&restype=container&renew
        [Update] establishes and manages a lock on a container for delete operations. The lock duration can be 15 to 60 seconds, or can be infinite
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.renew_container_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def break_container_lease(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        lease_break_period: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?comp=lease&restype=container&break
        [Update] establishes and manages a lock on a container for delete operations. The lock duration can be 15 to 60 seconds, or can be infinite
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.break_container_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def change_container_lease(
        self,
        container_name: str,
        lease_id: str,
        proposed_lease_id: str,
        timeout: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}?comp=lease&restype=container&change
        [Update] establishes and manages a lock on a container for delete operations. The lock duration can be 15 to 60 seconds, or can be infinite
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.change_container_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def list_blobs(
        self,
        container_name: str,
        prefix: Optional[str] = None,
        marker: Optional[str] = None,
        maxresults: Optional[int] = None,
        include: Optional[List[str]] = None,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}?restype=container&comp=list&flat
        [Update] The List Blobs operation returns a list of the blobs under the specified container
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.list_blobs(
                prefix=prefix,
                marker=marker,
                maxresults=maxresults,
                include=include,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_account_info_from_container(
        self,
        container_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}?restype=account&comp=properties
        Returns the sku name and account kind
        """
        try:
            client = await self._get_async_blob_service_client()
            container_client = client.get_container_client(container_name)
            request_headers = self._build_headers(headers, **kwargs)
            result = await container_client.get_account_info_from_container(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def download_blob(
        self,
        container_name: str,
        blob_name: str,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        timeout: Optional[int] = None,
        range: Optional[str] = None,
        lease_id: Optional[str] = None,
        range_get_content_md5: Optional[bool] = None,
        range_get_content_crc64: Optional[bool] = None,
        structured_body: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}/{blob}
        The Download operation reads or downloads a blob from the system, including its metadata and properties. You can also call Download to read a snapshot.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.download_blob(
                snapshot=snapshot,
                versionid=versionid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_blob_properties(
        self,
        container_name: str,
        blob_name: str,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        HEAD /{containerName}/{blob}
        The Get Properties operation returns all user-defined metadata, standard HTTP properties, and system properties for the blob. It does not return the content of the blob.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.get_blob_properties(
                snapshot=snapshot,
                versionid=versionid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def delete_blob(
        self,
        container_name: str,
        blob_name: str,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        delete_snapshots: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        deletetype: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        DELETE /{containerName}/{blob}
        If the storage account's soft delete feature is disabled then, when a blob is deleted, it is permanently removed from the storage account. If the storage account's soft delete feature is enabled, then, when a blob is deleted, it is marked for deletion and becomes inaccessible immediately. However, the blob service retains the blob or snapshot for the number of days specified by the DeleteRetentionPolicy section of [Storage service properties] (Set-Blob-Service-Properties.md). After the specified number of days has passed, the blob's data is permanently removed from the storage account. Note that you continue to be charged for the soft-deleted blob's storage until it is permanently removed. Use the List Blobs API and specify the "include=deleted" query parameter to discover which blobs and snapshots have been soft deleted. You can then use the Undelete Blob API to restore a soft-deleted blob. All other operations on a soft-deleted blob or snapshot causes the service to return an HTTP status code of 404 (ResourceNotFound).
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.delete_blob(
                snapshot=snapshot,
                versionid=versionid,
                deletetype=deletetype,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def create_page_blob(
        self,
        container_name: str,
        blob_name: str,
        Content_Length: int,
        blob_content_length: int,
        timeout: Optional[int] = None,
        access_tier: Optional[str] = None,
        blob_content_type: Optional[str] = None,
        blob_content_encoding: Optional[str] = None,
        blob_content_language: Optional[str] = None,
        blob_content_md5: Optional[bytes] = None,
        blob_cache_control: Optional[str] = None,
        meta: Optional[str] = None,
        lease_id: Optional[str] = None,
        blob_content_disposition: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        blob_sequence_number: Optional[int] = 0,
        client_request_id: Optional[str] = None,
        tags: Optional[str] = None,
        immutability_policy_until_date: Optional[datetime] = None,
        immutability_policy_mode: Optional[str] = None,
        legal_hold: Optional[bool] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?PageBlob
        The Create operation creates a new page blob.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.create_page_blob(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def create_append_blob(
        self,
        container_name: str,
        blob_name: str,
        Content_Length: int,
        timeout: Optional[int] = None,
        blob_content_type: Optional[str] = None,
        blob_content_encoding: Optional[str] = None,
        blob_content_language: Optional[str] = None,
        blob_content_md5: Optional[bytes] = None,
        blob_cache_control: Optional[str] = None,
        meta: Optional[str] = None,
        lease_id: Optional[str] = None,
        blob_content_disposition: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        tags: Optional[str] = None,
        immutability_policy_until_date: Optional[datetime] = None,
        immutability_policy_mode: Optional[str] = None,
        legal_hold: Optional[bool] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?AppendBlob
        The Create Append Blob operation creates a new append blob.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.create_append_blob(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def upload_blob(
        self,
        container_name: str,
        blob_name: str,
        body: str,
        Content_Length: int,
        timeout: Optional[int] = None,
        Content_MD5: Optional[bytes] = None,
        blob_content_type: Optional[str] = None,
        blob_content_encoding: Optional[str] = None,
        blob_content_language: Optional[str] = None,
        blob_content_md5: Optional[bytes] = None,
        blob_cache_control: Optional[str] = None,
        meta: Optional[str] = None,
        lease_id: Optional[str] = None,
        blob_content_disposition: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        access_tier: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        tags: Optional[str] = None,
        immutability_policy_until_date: Optional[datetime] = None,
        immutability_policy_mode: Optional[str] = None,
        legal_hold: Optional[bool] = None,
        content_crc64: Optional[bytes] = None,
        structured_body: Optional[str] = None,
        structured_content_length: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?BlockBlob
        The Upload Block Blob operation updates the content of an existing block blob. Updating an existing block blob overwrites any existing metadata on the blob. Partial updates are not supported with Put Blob; the content of the existing blob is overwritten with the content of the new blob. To perform a partial update of the content of a block blob, use the Put Block List operation.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.upload_blob(
                body=body,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def put_block_blob_from_url(
        self,
        container_name: str,
        blob_name: str,
        Content_Length: int,
        copy_source: str,
        timeout: Optional[int] = None,
        Content_MD5: Optional[bytes] = None,
        blob_content_type: Optional[str] = None,
        blob_content_encoding: Optional[str] = None,
        blob_content_language: Optional[str] = None,
        blob_content_md5: Optional[bytes] = None,
        blob_cache_control: Optional[str] = None,
        meta: Optional[str] = None,
        lease_id: Optional[str] = None,
        blob_content_disposition: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        access_tier: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        source_if_modified_since: Optional[datetime] = None,
        source_if_unmodified_since: Optional[datetime] = None,
        source_if_match: Optional[str] = None,
        source_if_none_match: Optional[str] = None,
        source_if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        source_content_md5: Optional[bytes] = None,
        tags: Optional[str] = None,
        copy_source_blob_properties: Optional[bool] = None,
        copy_source_authorization: Optional[str] = None,
        copy_source_tag_option: Optional[str] = None,
        file_request_intent: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?BlockBlob&fromUrl
        The Put Blob from URL operation creates a new Block Blob where the contents of the blob are read from a given URL.  This API is supported beginning with the 2020-04-08 version. Partial updates are not supported with Put Blob from URL; the content of an existing blob is overwritten with the content of the new blob.  To perform partial updates to a block blob’s contents using a source URL, use the Put Block from URL API in conjunction with Put Block List.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.put_block_blob_from_url(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def undelete_blob(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=undelete
        Undelete a blob that was previously soft deleted
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.undelete_blob(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_blob_expiry(
        self,
        container_name: str,
        blob_name: str,
        expiry_option: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        expiry_time: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=expiry
        Sets the time a blob will expire and be deleted.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.set_blob_expiry(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_blob_http_headers(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        blob_cache_control: Optional[str] = None,
        blob_content_type: Optional[str] = None,
        blob_content_md5: Optional[bytes] = None,
        blob_content_encoding: Optional[str] = None,
        blob_content_language: Optional[str] = None,
        lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        blob_content_disposition: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=properties&SetHTTPHeaders
        The Set HTTP Headers operation sets system properties on the blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.set_blob_http_headers(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_blob_immutability_policy(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        immutability_policy_until_date: Optional[datetime] = None,
        immutability_policy_mode: Optional[str] = None,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=immutabilityPolicies
        The Set Immutability Policy operation sets the immutability policy on the blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.set_blob_immutability_policy(
                snapshot=snapshot,
                versionid=versionid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def delete_blob_immutability_policy(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        DELETE /{containerName}/{blob}?comp=immutabilityPolicies
        The Delete Immutability Policy operation deletes the immutability policy on the blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.delete_blob_immutability_policy(
                snapshot=snapshot,
                versionid=versionid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_blob_legal_hold(
        self,
        container_name: str,
        blob_name: str,
        legal_hold: bool,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=legalhold
        The Set Legal Hold operation sets a legal hold on the blob.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.set_blob_legal_hold(
                snapshot=snapshot,
                versionid=versionid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_blob_metadata(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        meta: Optional[str] = None,
        lease_id: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=metadata
        The Set Blob Metadata operation sets user-defined metadata for the specified blob as one or more name-value pairs
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.set_blob_metadata(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def acquire_blob_lease(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        lease_duration: Optional[int] = None,
        proposed_lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=lease&acquire
        [Update] The Lease Blob operation establishes and manages a lock on a blob for write and delete operations
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.acquire_blob_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def release_blob_lease(
        self,
        container_name: str,
        blob_name: str,
        lease_id: str,
        timeout: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=lease&release
        [Update] The Lease Blob operation establishes and manages a lock on a blob for write and delete operations
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.release_blob_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def renew_blob_lease(
        self,
        container_name: str,
        blob_name: str,
        lease_id: str,
        timeout: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=lease&renew
        [Update] The Lease Blob operation establishes and manages a lock on a blob for write and delete operations
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.renew_blob_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def change_blob_lease(
        self,
        container_name: str,
        blob_name: str,
        lease_id: str,
        proposed_lease_id: str,
        timeout: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=lease&change
        [Update] The Lease Blob operation establishes and manages a lock on a blob for write and delete operations
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.change_blob_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def break_blob_lease(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        lease_break_period: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=lease&break
        [Update] The Lease Blob operation establishes and manages a lock on a blob for write and delete operations
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.break_blob_lease(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def create_blob_snapshot(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        meta: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        lease_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=snapshot
        The Create Snapshot operation creates a read-only snapshot of a blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.create_blob_snapshot(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def start_copy_blob_from_url(
        self,
        container_name: str,
        blob_name: str,
        copy_source: str,
        timeout: Optional[int] = None,
        meta: Optional[str] = None,
        access_tier: Optional[str] = None,
        rehydrate_priority: Optional[str] = None,
        source_if_modified_since: Optional[datetime] = None,
        source_if_unmodified_since: Optional[datetime] = None,
        source_if_match: Optional[str] = None,
        source_if_none_match: Optional[str] = None,
        source_if_tags: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        lease_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        tags: Optional[str] = None,
        seal_blob: Optional[bool] = None,
        immutability_policy_until_date: Optional[datetime] = None,
        immutability_policy_mode: Optional[str] = None,
        legal_hold: Optional[bool] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=copy
        The Start Copy From URL operation copies a blob or an internet resource to a new blob.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.start_copy_blob_from_url(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def copy_blob_from_url(
        self,
        container_name: str,
        blob_name: str,
        copy_source: str,
        timeout: Optional[int] = None,
        meta: Optional[str] = None,
        access_tier: Optional[str] = None,
        source_if_modified_since: Optional[datetime] = None,
        source_if_unmodified_since: Optional[datetime] = None,
        source_if_match: Optional[str] = None,
        source_if_none_match: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        lease_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        source_content_md5: Optional[bytes] = None,
        tags: Optional[str] = None,
        immutability_policy_until_date: Optional[datetime] = None,
        immutability_policy_mode: Optional[str] = None,
        legal_hold: Optional[bool] = None,
        copy_source_authorization: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        copy_source_tag_option: Optional[str] = None,
        file_request_intent: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=copy&sync
        The Copy From URL operation copies a blob or an internet resource to a new blob. It will not return a response until the copy is complete.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.copy_blob_from_url(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def abort_copy_blob_from_url(
        self,
        container_name: str,
        blob_name: str,
        copyid: str,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=copy&copyid
        The Abort Copy From URL operation aborts a pending Copy From URL operation, and leaves a destination blob with zero length and full metadata.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.abort_copy_blob_from_url(
                copyid=copyid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_blob_tier(
        self,
        container_name: str,
        blob_name: str,
        access_tier: str,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        timeout: Optional[int] = None,
        rehydrate_priority: Optional[str] = None,
        client_request_id: Optional[str] = None,
        lease_id: Optional[str] = None,
        if_tags: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=tier
        The Set Tier operation sets the tier on a blob. The operation is allowed on a page blob in a premium storage account and on a block blob in a blob storage account (locally redundant storage only). A premium page blob's tier determines the allowed size, IOPS, and bandwidth of the blob. A block blob's tier determines Hot/Cool/Archive storage type. This operation does not update the blob's ETag.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.set_blob_tier(
                snapshot=snapshot,
                versionid=versionid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_account_info_from_blob(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}/{blob}?restype=account&comp=properties&blob
        Returns the sku name and account kind
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.get_account_info_from_blob(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def stage_block(
        self,
        container_name: str,
        blob_name: str,
        blockid: str,
        Content_Length: int,
        body: str,
        Content_MD5: Optional[bytes] = None,
        content_crc64: Optional[bytes] = None,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        client_request_id: Optional[str] = None,
        structured_body: Optional[str] = None,
        structured_content_length: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=block
        The Stage Block operation creates a new block to be committed as part of a blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.stage_block(
                blockid=blockid,
                body=body,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def stage_block_from_url(
        self,
        container_name: str,
        blob_name: str,
        blockid: str,
        Content_Length: int,
        copy_source: str,
        source_range: Optional[str] = None,
        source_content_md5: Optional[bytes] = None,
        source_content_crc64: Optional[bytes] = None,
        timeout: Optional[int] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        lease_id: Optional[str] = None,
        source_if_modified_since: Optional[datetime] = None,
        source_if_unmodified_since: Optional[datetime] = None,
        source_if_match: Optional[str] = None,
        source_if_none_match: Optional[str] = None,
        client_request_id: Optional[str] = None,
        copy_source_authorization: Optional[str] = None,
        file_request_intent: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=block&fromURL
        The Stage Block operation creates a new block to be committed as part of a blob where the contents are read from a URL.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.stage_block_from_url(
                blockid=blockid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def commit_block_list(
        self,
        container_name: str,
        blob_name: str,
        blocks: str,
        timeout: Optional[int] = None,
        blob_cache_control: Optional[str] = None,
        blob_content_type: Optional[str] = None,
        blob_content_encoding: Optional[str] = None,
        blob_content_language: Optional[str] = None,
        blob_content_md5: Optional[bytes] = None,
        Content_MD5: Optional[bytes] = None,
        content_crc64: Optional[bytes] = None,
        meta: Optional[str] = None,
        lease_id: Optional[str] = None,
        blob_content_disposition: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        access_tier: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        tags: Optional[str] = None,
        immutability_policy_until_date: Optional[datetime] = None,
        immutability_policy_mode: Optional[str] = None,
        legal_hold: Optional[bool] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=blocklist
        The Commit Block List operation writes a blob by specifying the list of block IDs that make up the blob. In order to be written as part of a blob, a block must have been successfully written to the server in a prior Put Block operation. You can call Put Block List to update a blob by uploading only those blocks that have changed, then committing the new and existing blocks together. You can do this by specifying whether to commit a block from the committed block list or from the uncommitted block list, or to commit the most recently uploaded version of the block, whichever list it may belong to.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.commit_block_list(
                blocks=blocks,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_block_list(
        self,
        container_name: str,
        blob_name: str,
        blocklisttype: str,
        snapshot: Optional[str] = None,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}/{blob}?comp=blocklist
        The Get Block List operation retrieves the list of blocks that have been uploaded as part of a block blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.get_block_list(
                snapshot=snapshot,
                blocklisttype=blocklisttype,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def upload_pages(
        self,
        container_name: str,
        blob_name: str,
        body: str,
        Content_Length: int,
        Content_MD5: Optional[bytes] = None,
        content_crc64: Optional[bytes] = None,
        timeout: Optional[int] = None,
        range: Optional[str] = None,
        lease_id: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        if_sequence_number_le: Optional[int] = None,
        if_sequence_number_lt: Optional[int] = None,
        if_sequence_number_eq: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        structured_body: Optional[str] = None,
        structured_content_length: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=page&update
        The Upload Pages operation writes a range of pages to a page blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.upload_pages(
                body=body,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def clear_pages(
        self,
        container_name: str,
        blob_name: str,
        Content_Length: int,
        timeout: Optional[int] = None,
        range: Optional[str] = None,
        lease_id: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        if_sequence_number_le: Optional[int] = None,
        if_sequence_number_lt: Optional[int] = None,
        if_sequence_number_eq: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=page&clear
        The Clear Pages operation clears a set of pages from a page blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.clear_pages(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def upload_pages_from_url(
        self,
        container_name: str,
        blob_name: str,
        copy_source: str,
        source_range: str,
        Content_Length: int,
        range: str,
        source_content_md5: Optional[bytes] = None,
        source_content_crc64: Optional[bytes] = None,
        timeout: Optional[int] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        lease_id: Optional[str] = None,
        if_sequence_number_le: Optional[int] = None,
        if_sequence_number_lt: Optional[int] = None,
        if_sequence_number_eq: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        source_if_modified_since: Optional[datetime] = None,
        source_if_unmodified_since: Optional[datetime] = None,
        source_if_match: Optional[str] = None,
        source_if_none_match: Optional[str] = None,
        client_request_id: Optional[str] = None,
        copy_source_authorization: Optional[str] = None,
        file_request_intent: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=page&update&fromUrl
        The Upload Pages operation writes a range of pages to a page blob where the contents are read from a URL
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.upload_pages_from_url(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_page_ranges(
        self,
        container_name: str,
        blob_name: str,
        snapshot: Optional[str] = None,
        timeout: Optional[int] = None,
        range: Optional[str] = None,
        lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        marker: Optional[str] = None,
        maxresults: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}/{blob}?comp=pagelist
        The Get Page Ranges operation returns the list of valid page ranges for a page blob or snapshot of a page blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.get_page_ranges(
                snapshot=snapshot,
                marker=marker,
                maxresults=maxresults,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_page_ranges_diff(
        self,
        container_name: str,
        blob_name: str,
        snapshot: Optional[str] = None,
        timeout: Optional[int] = None,
        prevsnapshot: Optional[str] = None,
        previous_snapshot_url: Optional[str] = None,
        range: Optional[str] = None,
        lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        marker: Optional[str] = None,
        maxresults: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}/{blob}?comp=pagelist&diff
        The Get Page Ranges Diff operation returns the list of valid page ranges for a page blob that were changed between target blob and previous snapshot.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.get_page_ranges_diff(
                snapshot=snapshot,
                prevsnapshot=prevsnapshot,
                marker=marker,
                maxresults=maxresults,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def resize_page_blob(
        self,
        container_name: str,
        blob_name: str,
        blob_content_length: int,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=properties&Resize
        Resize the Blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.resize_page_blob(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def update_page_blob_sequence_number(
        self,
        container_name: str,
        blob_name: str,
        sequence_number_action: str,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        blob_sequence_number: Optional[int] = 0,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=properties&UpdateSequenceNumber
        Update the sequence number of the blob
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.update_page_blob_sequence_number(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def incremental_copy_blob(
        self,
        container_name: str,
        blob_name: str,
        copy_source: str,
        timeout: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=incrementalcopy
        The Copy Incremental operation copies a snapshot of the source page blob to a destination page blob. The snapshot is copied such that only the differential changes between the previously copied snapshot are transferred to the destination. The copied snapshots are complete copies of the original snapshot and can be read or copied from as usual. This API is supported since REST version 2016-05-31.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.incremental_copy_blob(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def append_block(
        self,
        container_name: str,
        blob_name: str,
        body: str,
        Content_Length: int,
        timeout: Optional[int] = None,
        Content_MD5: Optional[bytes] = None,
        content_crc64: Optional[bytes] = None,
        lease_id: Optional[str] = None,
        blob_condition_maxsize: Optional[int] = None,
        blob_condition_appendpos: Optional[int] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        structured_body: Optional[str] = None,
        structured_content_length: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=appendblock
        The Append Block operation commits a new block of data to the end of an existing append blob. The Append Block operation is permitted only if the blob was created with x-ms-blob-type set to AppendBlob. Append Block is supported only on version 2015-02-21 version or later.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.append_block(
                body=body,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def append_block_from_url(
        self,
        container_name: str,
        blob_name: str,
        copy_source: str,
        Content_Length: int,
        source_range: Optional[str] = None,
        source_content_md5: Optional[bytes] = None,
        source_content_crc64: Optional[bytes] = None,
        timeout: Optional[int] = None,
        Content_MD5: Optional[bytes] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        encryption_scope: Optional[str] = None,
        lease_id: Optional[str] = None,
        blob_condition_maxsize: Optional[int] = None,
        blob_condition_appendpos: Optional[int] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        source_if_modified_since: Optional[datetime] = None,
        source_if_unmodified_since: Optional[datetime] = None,
        source_if_match: Optional[str] = None,
        source_if_none_match: Optional[str] = None,
        client_request_id: Optional[str] = None,
        copy_source_authorization: Optional[str] = None,
        file_request_intent: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=appendblock&fromUrl
        The Append Block operation commits a new block of data to the end of an existing append blob where the contents are read from a source url. The Append Block operation is permitted only if the blob was created with x-ms-blob-type set to AppendBlob. Append Block is supported only on version 2015-02-21 version or later.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.append_block_from_url(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def seal_append_blob(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        lease_id: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        blob_condition_appendpos: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=seal
        The Seal operation seals the Append Blob to make it read-only. Seal is supported only on version 2019-12-12 version or later.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.seal_append_blob(
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def query_blob_contents(
        self,
        container_name: str,
        blob_name: str,
        queryRequest: Optional[str] = None,
        snapshot: Optional[str] = None,
        timeout: Optional[int] = None,
        lease_id: Optional[str] = None,
        encryption_key: Optional[str] = None,
        encryption_key_sha256: Optional[str] = None,
        encryption_algorithm: Optional[str] = None,
        If_Modified_Since: Optional[datetime] = None,
        If_Unmodified_Since: Optional[datetime] = None,
        If_Match: Optional[str] = None,
        If_None_Match: Optional[str] = None,
        if_tags: Optional[str] = None,
        client_request_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        POST /{containerName}/{blob}?comp=query
        The Query operation enables users to select/project on blob data by providing simple query expressions.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.query_blob_contents(
                queryRequest=queryRequest,
                snapshot=snapshot,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def get_blob_tags(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        client_request_id: Optional[str] = None,
        snapshot: Optional[str] = None,
        versionid: Optional[str] = None,
        if_tags: Optional[str] = None,
        lease_id: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        GET /{containerName}/{blob}?comp=tags
        The Get Tags operation enables users to get the tags associated with a blob.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                snapshot=snapshot if "snapshot" in locals() else None,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.get_blob_tags(
                snapshot=snapshot,
                versionid=versionid,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))

    async def set_blob_tags(
        self,
        container_name: str,
        blob_name: str,
        timeout: Optional[int] = None,
        versionid: Optional[str] = None,
        Content_MD5: Optional[bytes] = None,
        content_crc64: Optional[bytes] = None,
        client_request_id: Optional[str] = None,
        if_tags: Optional[str] = None,
        lease_id: Optional[str] = None,
        Tags: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        PUT /{containerName}/{blob}?comp=tags
        The Set Tags operation enables users to set tags on a blob.
        """
        try:
            client = await self._get_async_blob_service_client()
            blob_client = client.get_blob_client(
                container=container_name,
                blob=blob_name,
                versionid=versionid if "versionid" in locals() else None
            )
            request_headers = self._build_headers(headers, **kwargs)
            result = await blob_client.set_blob_tags(
                versionid=versionid,
                Tags=Tags,
                timeout=timeout,
                headers=request_headers
            )

            return AzureBlobResponse(success=True, data=result)
        except Exception as e:
            return AzureBlobResponse(success=False, error=str(e))


    # =================================
    # 🔗 SAS URL GENERATION METHODS
    # =================================

    async def generate_account_sas_url(
        self,
        resource_types: str,
        permission: str,
        expiry: datetime,
        start: Optional[datetime] = None,
        ip: Optional[str] = None,
        protocol: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        Generate an Account-level SAS URL for Azure Blob Storage.
        Args:
            resource_types (str): Resource types (s=service, c=container, o=object)
            permission (str): Permissions (r=read, w=write, d=delete, l=list, etc.)
            expiry (datetime): Expiration time for the SAS URL
            start (Optional[datetime]): Start time for SAS validity
            ip (Optional[str]): IP address or range to restrict access
            protocol (Optional[str]): Protocol (https/http)
            headers (Optional[Dict[str, str]]): Additional headers
        Returns:
            AzureBlobResponse: Response containing the SAS URL
        """
        try:
            client = await self._get_async_blob_service_client()
            account_name = client.account_name
            account_key = client.credential.account_key if hasattr(client.credential, "account_key") else None

            if not account_key:
                return AzureBlobResponse(
                    success=False,
                    error="Account key required for SAS generation. Use connection string or account key authentication."
                )

            sas_token = generate_account_sas(
                account_name=account_name,
                account_key=account_key,
                resource_types=ResourceTypes.from_string(resource_types),
                permission=AccountSasPermissions.from_string(permission),
                expiry=expiry,
                start=start,
                ip=ip,
                protocol=protocol
            )

            account_url = f"https://{account_name}.blob.core.windows.net"
            sas_url = f"{account_url}?{sas_token}"

            return AzureBlobResponse(
                success=True,
                data={
                    "sas_url": sas_url,
                    "sas_token": sas_token,
                    "account_url": account_url,
                    "expiry": expiry.isoformat(),
                    "permissions": permission,
                    "resource_types": resource_types
                }
            )
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"SAS generation failed: {str(e)}")

    async def generate_container_sas_url(
        self,
        container_name: str,
        permission: str,
        expiry: datetime,
        start: Optional[datetime] = None,
        policy_id: Optional[str] = None,
        ip: Optional[str] = None,
        protocol: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        Generate a Container-level SAS URL.
        Args:
            container_name (str): Name of the container
            permission (str): Permissions (r=read, w=write, d=delete, l=list, etc.)
            expiry (datetime): Expiration time for the SAS URL
            start (Optional[datetime]): Start time for SAS validity
            policy_id (Optional[str]): Stored access policy ID
            ip (Optional[str]): IP address or range to restrict access
            protocol (Optional[str]): Protocol (https/http)
            headers (Optional[Dict[str, str]]): Additional headers
        Returns:
            AzureBlobResponse: Response containing the SAS URL
        """
        try:
            client = await self._get_async_blob_service_client()
            account_name = client.account_name
            account_key = client.credential.account_key if hasattr(client.credential, "account_key") else None

            if not account_key:
                return AzureBlobResponse(
                    success=False,
                    error="Account key required for SAS generation. Use connection string or account key authentication."
                )

            sas_token = generate_container_sas(
                account_name=account_name,
                container_name=container_name,
                account_key=account_key,
                permission=ContainerSasPermissions.from_string(permission) if not policy_id else None,
                expiry=expiry,
                start=start,
                policy_id=policy_id,
                ip=ip,
                protocol=protocol
            )

            container_url = f"https://{account_name}.blob.core.windows.net/{container_name}"
            sas_url = f"{container_url}?{sas_token}"

            return AzureBlobResponse(
                success=True,
                data={
                    "sas_url": sas_url,
                    "sas_token": sas_token,
                    "container_url": container_url,
                    "container_name": container_name,
                    "expiry": expiry.isoformat(),
                    "permissions": permission
                }
            )
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Container SAS generation failed: {str(e)}")

    async def generate_blob_sas_url(
        self,
        container_name: str,
        blob_name: str,
        permission: str,
        expiry: datetime,
        start: Optional[datetime] = None,
        policy_id: Optional[str] = None,
        ip: Optional[str] = None,
        protocol: Optional[str] = None,
        cache_control: Optional[str] = None,
        content_disposition: Optional[str] = None,
        content_encoding: Optional[str] = None,
        content_language: Optional[str] = None,
        content_type: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        Generate a Blob-level SAS URL.
        Args:
            container_name (str): Name of the container
            blob_name (str): Name of the blob
            permission (str): Permissions (r=read, w=write, d=delete, etc.)
            expiry (datetime): Expiration time for the SAS URL
            start (Optional[datetime]): Start time for SAS validity
            policy_id (Optional[str]): Stored access policy ID
            ip (Optional[str]): IP address or range to restrict access
            protocol (Optional[str]): Protocol (https/http)
            cache_control (Optional[str]): Cache control header override
            content_disposition (Optional[str]): Content disposition header override
            content_encoding (Optional[str]): Content encoding header override
            content_language (Optional[str]): Content language header override
            content_type (Optional[str]): Content type header override
            headers (Optional[Dict[str, str]]): Additional headers
        Returns:
            AzureBlobResponse: Response containing the SAS URL
        """
        try:
            client = await self._get_async_blob_service_client()
            account_name = client.account_name
            account_key = client.credential.account_key if hasattr(client.credential, "account_key") else None

            if not account_key:
                return AzureBlobResponse(
                    success=False,
                    error="Account key required for SAS generation. Use connection string or account key authentication."
                )

            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_name,
                account_key=account_key,
                permission=BlobSasPermissions.from_string(permission) if not policy_id else None,
                expiry=expiry,
                start=start,
                policy_id=policy_id,
                ip=ip,
                protocol=protocol,
                cache_control=cache_control,
                content_disposition=content_disposition,
                content_encoding=content_encoding,
                content_language=content_language,
                content_type=content_type
            )

            blob_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}"
            sas_url = f"{blob_url}?{sas_token}"

            return AzureBlobResponse(
                success=True,
                data={
                    "sas_url": sas_url,
                    "sas_token": sas_token,
                    "blob_url": blob_url,
                    "container_name": container_name,
                    "blob_name": blob_name,
                    "expiry": expiry.isoformat(),
                    "permissions": permission
                }
            )
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Blob SAS generation failed: {str(e)}")

    async def generate_user_delegation_sas_url(
        self,
        container_name: str,
        blob_name: str,
        permission: str,
        expiry: datetime,
        user_delegation_key: Union[UserDelegationKey, Dict[str, Any]],
        start: Optional[datetime] = None,
        ip: Optional[str] = None,
        protocol: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> AzureBlobResponse:
        """
        Generate a User Delegation SAS URL (for Azure AD authentication).
        Args:
            container_name (str): Name of the container
            blob_name (str): Name of the blob
            permission (str): Permissions (r=read, w=write, d=delete, etc.)
            expiry (datetime): Expiration time for the SAS URL
            user_delegation_key (Dict[str, Any]): User delegation key from get_user_delegation_key()
            start (Optional[datetime]): Start time for SAS validity
            ip (Optional[str]): IP address or range to restrict access
            protocol (Optional[str]): Protocol (https/http)
            headers (Optional[Dict[str, str]]): Additional headers
        Returns:
            AzureBlobResponse: Response containing the SAS URL
        """
        try:
            client = await self._get_async_blob_service_client()
            account_name = client.account_name

            # Convert dict to UserDelegationKey object if needed
            if isinstance(user_delegation_key, dict):
                try:
                    delegation_key = UserDelegationKey()
                    delegation_key.signed_oid = user_delegation_key['signed_oid']
                    delegation_key.signed_tid = user_delegation_key['signed_tid']
                    delegation_key.signed_start = user_delegation_key['signed_start']
                    delegation_key.signed_expiry = user_delegation_key['signed_expiry']
                    delegation_key.signed_service = user_delegation_key['signed_service']
                    delegation_key.signed_version = user_delegation_key['signed_version']
                    delegation_key.value = user_delegation_key['value']
                except KeyError as e:
                    return AzureBlobResponse(success=False, error=f"User delegation key dictionary is missing required key: {e}")
            else:
                delegation_key = user_delegation_key

            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_name,
                user_delegation_key=delegation_key,
                permission=BlobSasPermissions.from_string(permission),
                expiry=expiry,
                start=start,
                ip=ip,
                protocol=protocol
            )

            blob_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}"
            sas_url = f"{blob_url}?{sas_token}"

            return AzureBlobResponse(
                success=True,
                data={
                    "sas_url": sas_url,
                    "sas_token": sas_token,
                    "blob_url": blob_url,
                    "container_name": container_name,
                    "blob_name": blob_name,
                    "expiry": expiry.isoformat(),
                    "permissions": permission,
                    "delegation_key_used": True
                }
            )
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"User delegation SAS generation failed: {str(e)}")

    async def close_async_client(self) -> None:
        """Close the async client if it exists."""
        if self._async_blob_service_client:
            await self._async_blob_service_client.close()
            self._async_blob_service_client = None

    async def __aenter__(self) -> "AzureBlobDataSource":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit with cleanup."""
        await self.close_async_client()
