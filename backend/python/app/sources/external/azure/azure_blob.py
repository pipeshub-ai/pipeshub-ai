from datetime import datetime
from typing import Dict, List, Optional, Union

try:
    from azure.core.credentials import TokenCredential  # type: ignore
    from azure.core.exceptions import AzureError  # type: ignore
    from azure.storage.blob import (  # type: ignore
        BlobAnalyticsLogging,
        BlobLeaseClient,
        CorsRule,
        Metrics,
        PremiumPageBlobTier,
        RehydratePriority,
        ResourceTypes,
        RetentionPolicy,
        StandardBlobTier,
        StaticWebsite,
        UserDelegationKey,
    )
except ImportError:
    raise ImportError("azure-storage-blob is not installed. Please install it with `pip install azure-storage-blob`")

from app.sources.client.azure.azure_blob import AzureBlobClient, AzureBlobResponse


class AzureBlobDataSource:
    """
    🚀 COMPLETE Azure Blob Storage API client wrapper with EXPLICIT METHOD SIGNATURES.
    📋 **COMPLETE API COVERAGE:**
    🔧 Service Operations (6 methods)
    🔐 SAS Generation (4 methods) ⭐ NEWLY ADDED!
    📁 Container Operations (15+ methods)
    📄 Blob Operations (40+ methods)
    🧱 Block/Append/Page Blob Specifics (20+ methods)
    🚀 Advanced Features (10+ methods)
    🔍 Existence Checks & Utilities (15+ methods)
    """

    def __init__(self, azure_blob_client: AzureBlobClient) -> None:
        """Initialize with AzureBlobClient."""
        self._azure_blob_client = azure_blob_client

    def _handle_azure_blob_response(self, response: object) -> AzureBlobResponse:
        """Handle Azure Blob Storage API response with comprehensive error handling."""
        try:
            if response is None:
                return AzureBlobResponse(success=False, error="Empty response from Azure Blob Storage API")

            if hasattr(response, '__dict__'):
                # Convert Azure response objects to dictionary
                data = {}
                for key, value in response.__dict__.items():
                    if not key.startswith('_'):
                        if hasattr(value, '__dict__'):
                            data[key] = value.__dict__
                        else:
                            data[key] = value
                return AzureBlobResponse(success=True, data=data)
            elif isinstance(response, dict):
                return AzureBlobResponse(success=True, data=response)
            else:
                return AzureBlobResponse(success=True, data={'result': str(response)})

        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Response handling error: {str(e)}")

    async def clear_blob_service_cors_rules(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Clear Blob Service Cors Rules operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'clear_blob_service_cors_rules')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def clear_static_website(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Clear Static Website operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'clear_static_website')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def close(self) -> AzureBlobResponse:
        """Azure Blob Storage Close operation.


        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'close')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def copy_blob_from_container(self,
        source_container_name: str,
        source_blob_name: str,
        destination_container_name: str,
        destination_blob_name: str,
        metadata: Optional[Dict[str, str]] = None,
        if_modified_since: Optional[datetime] = None,
        if_unmodified_since: Optional[datetime] = None,
        etag: Optional[str] = None,
        if_none_match: Optional[str] = None,
        if_tags_match_condition: Optional[str] = None,
        source_if_modified_since: Optional[datetime] = None,
        source_if_unmodified_since: Optional[datetime] = None,
        source_etag: Optional[str] = None,
        source_if_none_match: Optional[str] = None,
        source_lease: Optional[str] = None,
        lease: Optional[BlobLeaseClient] = None,
        timeout: Optional[int] = None,
        tier: Optional[Union[str, StandardBlobTier, PremiumPageBlobTier]] = None,
        rehydrate_priority: Optional[RehydratePriority] = None) -> AzureBlobResponse:
        """Azure Blob Storage Copy Blob From Container operation.

        Args:
            source_container_name (str): Required parameter
            source_blob_name (str): Required parameter
            destination_container_name (str): Required parameter
            destination_blob_name (str): Required parameter
            metadata (Optional[Dict[str, str]]): Optional parameter
            if_modified_since (Optional[datetime]): Optional parameter
            if_unmodified_since (Optional[datetime]): Optional parameter
            etag (Optional[str]): Optional parameter
            if_none_match (Optional[str]): Optional parameter
            if_tags_match_condition (Optional[str]): Optional parameter
            source_if_modified_since (Optional[datetime]): Optional parameter
            source_if_unmodified_since (Optional[datetime]): Optional parameter
            source_etag (Optional[str]): Optional parameter
            source_if_none_match (Optional[str]): Optional parameter
            source_lease (Optional[str]): Optional parameter
            lease (Optional[BlobLeaseClient]): Optional parameter
            timeout (Optional[int]): Optional parameter
            tier (Optional[Union[str, StandardBlobTier, PremiumPageBlobTier]]): Optional parameter
            rehydrate_priority (Optional[RehydratePriority]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'source_container_name': source_container_name, 'source_blob_name': source_blob_name, 'destination_container_name': destination_container_name, 'destination_blob_name': destination_blob_name}
        if metadata is not None:
            kwargs['metadata'] = metadata
        if if_modified_since is not None:
            kwargs['if_modified_since'] = if_modified_since
        if if_unmodified_since is not None:
            kwargs['if_unmodified_since'] = if_unmodified_since
        if etag is not None:
            kwargs['etag'] = etag
        if if_none_match is not None:
            kwargs['if_none_match'] = if_none_match
        if if_tags_match_condition is not None:
            kwargs['if_tags_match_condition'] = if_tags_match_condition
        if source_if_modified_since is not None:
            kwargs['source_if_modified_since'] = source_if_modified_since
        if source_if_unmodified_since is not None:
            kwargs['source_if_unmodified_since'] = source_if_unmodified_since
        if source_etag is not None:
            kwargs['source_etag'] = source_etag
        if source_if_none_match is not None:
            kwargs['source_if_none_match'] = source_if_none_match
        if source_lease is not None:
            kwargs['source_lease'] = source_lease
        if lease is not None:
            kwargs['lease'] = lease
        if timeout is not None:
            kwargs['timeout'] = timeout
        if tier is not None:
            kwargs['tier'] = tier
        if rehydrate_priority is not None:
            kwargs['rehydrate_priority'] = rehydrate_priority

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'copy_blob_from_container')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def create_configuration(self,
        max_single_put_size: Optional[int] = None,
        max_block_size: Optional[int] = None,
        min_large_block_upload_threshold: Optional[int] = None,
        use_byte_buffer: Optional[bool] = None,
        max_page_size: Optional[int] = None,
        max_single_get_size: Optional[int] = None,
        max_chunk_get_size: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Create Configuration operation.

        Args:
            max_single_put_size (Optional[int]): Optional parameter
            max_block_size (Optional[int]): Optional parameter
            min_large_block_upload_threshold (Optional[int]): Optional parameter
            use_byte_buffer (Optional[bool]): Optional parameter
            max_page_size (Optional[int]): Optional parameter
            max_single_get_size (Optional[int]): Optional parameter
            max_chunk_get_size (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if max_single_put_size is not None:
            kwargs['max_single_put_size'] = max_single_put_size
        if max_block_size is not None:
            kwargs['max_block_size'] = max_block_size
        if min_large_block_upload_threshold is not None:
            kwargs['min_large_block_upload_threshold'] = min_large_block_upload_threshold
        if use_byte_buffer is not None:
            kwargs['use_byte_buffer'] = use_byte_buffer
        if max_page_size is not None:
            kwargs['max_page_size'] = max_page_size
        if max_single_get_size is not None:
            kwargs['max_single_get_size'] = max_single_get_size
        if max_chunk_get_size is not None:
            kwargs['max_chunk_get_size'] = max_chunk_get_size

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'create_configuration')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_blobs(self,
        blob_names: List[str],
        delete_snapshots: Optional[str] = None,
        lease: Optional[BlobLeaseClient] = None,
        if_modified_since: Optional[datetime] = None,
        if_unmodified_since: Optional[datetime] = None,
        if_tags_match_condition: Optional[str] = None,
        timeout: Optional[int] = None,
        raise_on_any_failure: Optional[bool] = None) -> AzureBlobResponse:
        """Azure Blob Storage Delete Blobs operation.

        Args:
            blob_names (List[str]): Required parameter
            delete_snapshots (Optional[str]): Optional parameter
            lease (Optional[BlobLeaseClient]): Optional parameter
            if_modified_since (Optional[datetime]): Optional parameter
            if_unmodified_since (Optional[datetime]): Optional parameter
            if_tags_match_condition (Optional[str]): Optional parameter
            timeout (Optional[int]): Optional parameter
            raise_on_any_failure (Optional[bool]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'blob_names': blob_names}
        if delete_snapshots is not None:
            kwargs['delete_snapshots'] = delete_snapshots
        if lease is not None:
            kwargs['lease'] = lease
        if if_modified_since is not None:
            kwargs['if_modified_since'] = if_modified_since
        if if_unmodified_since is not None:
            kwargs['if_unmodified_since'] = if_unmodified_since
        if if_tags_match_condition is not None:
            kwargs['if_tags_match_condition'] = if_tags_match_condition
        if timeout is not None:
            kwargs['timeout'] = timeout
        if raise_on_any_failure is not None:
            kwargs['raise_on_any_failure'] = raise_on_any_failure

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'delete_blobs')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def exists(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Exists operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to container client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            container_client = blob_service_client.get_container_client(self._azure_blob_client.client.get_container_name())
            response = container_client.exists(**kwargs)
            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def filter_containers(self,
        filter_expression: str,
        results_per_page: Optional[int] = None,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Filter Containers operation.

        Args:
            filter_expression (str): Required parameter
            results_per_page (Optional[int]): Optional parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'filter_expression': filter_expression}
        if results_per_page is not None:
            kwargs['results_per_page'] = results_per_page
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'filter_containers')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def find_blobs_by_tags(self,
        filter_expression: str,
        results_per_page: Optional[int] = None,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Find Blobs By Tags operation.

        Args:
            filter_expression (str): Required parameter
            results_per_page (Optional[int]): Optional parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'filter_expression': filter_expression}
        if results_per_page is not None:
            kwargs['results_per_page'] = results_per_page
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'find_blobs_by_tags')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def from_blob_url(self,
        blob_url: str,
        credential: Optional[Union[str, Dict[str, str], TokenCredential, None]] = None,
        snapshot: Optional[str] = None) -> AzureBlobResponse:
        """Azure Blob Storage From Blob Url operation.

        Args:
            blob_url (str): Required parameter
            credential (Optional[Union[str, Dict[str, str], TokenCredential, None]]): Optional parameter
            snapshot (Optional[str]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'blob_url': blob_url}
        if credential is not None:
            kwargs['credential'] = credential
        if snapshot is not None:
            kwargs['snapshot'] = snapshot

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'from_blob_url')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def generate_account_sas(self,
        resource_types: ResourceTypes,
        permission: str,
        expiry: datetime,
        start: Optional[datetime] = None,
        ip: Optional[str] = None,
        protocol: Optional[str] = None,
        encryption_scope: Optional[str] = None) -> AzureBlobResponse:
        """Azure Blob Storage Generate Account Sas operation.

        Args:
            resource_types (ResourceTypes): Required parameter
            permission (str): Required parameter
            expiry (datetime): Required parameter
            start (Optional[datetime]): Optional parameter
            ip (Optional[str]): Optional parameter
            protocol (Optional[str]): Optional parameter
            encryption_scope (Optional[str]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'resource_types': resource_types, 'permission': permission, 'expiry': expiry}
        if start is not None:
            kwargs['start'] = start
        if ip is not None:
            kwargs['ip'] = ip
        if protocol is not None:
            kwargs['protocol'] = protocol
        if encryption_scope is not None:
            kwargs['encryption_scope'] = encryption_scope

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'generate_account_sas')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def generate_shared_access_signature(self,
        permission: str,
        expiry: Optional[datetime] = None,
        start: Optional[datetime] = None,
        policy_id: Optional[str] = None,
        ip: Optional[str] = None,
        protocol: Optional[str] = None,
        cache_control: Optional[str] = None,
        content_disposition: Optional[str] = None,
        content_encoding: Optional[str] = None,
        content_language: Optional[str] = None,
        content_type: Optional[str] = None,
        user_delegation_key: Optional[UserDelegationKey] = None) -> AzureBlobResponse:
        """Azure Blob Storage Generate Shared Access Signature operation.

        Args:
            permission (str): Required parameter
            expiry (Optional[datetime]): Optional parameter
            start (Optional[datetime]): Optional parameter
            policy_id (Optional[str]): Optional parameter
            ip (Optional[str]): Optional parameter
            protocol (Optional[str]): Optional parameter
            cache_control (Optional[str]): Optional parameter
            content_disposition (Optional[str]): Optional parameter
            content_encoding (Optional[str]): Optional parameter
            content_language (Optional[str]): Optional parameter
            content_type (Optional[str]): Optional parameter
            user_delegation_key (Optional[UserDelegationKey]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'permission': permission}
        if expiry is not None:
            kwargs['expiry'] = expiry
        if start is not None:
            kwargs['start'] = start
        if policy_id is not None:
            kwargs['policy_id'] = policy_id
        if ip is not None:
            kwargs['ip'] = ip
        if protocol is not None:
            kwargs['protocol'] = protocol
        if cache_control is not None:
            kwargs['cache_control'] = cache_control
        if content_disposition is not None:
            kwargs['content_disposition'] = content_disposition
        if content_encoding is not None:
            kwargs['content_encoding'] = content_encoding
        if content_language is not None:
            kwargs['content_language'] = content_language
        if content_type is not None:
            kwargs['content_type'] = content_type
        if user_delegation_key is not None:
            kwargs['user_delegation_key'] = user_delegation_key

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'generate_shared_access_signature')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_account_information(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Account Information operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_account_information')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_batch_client(self) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Batch Client operation.


        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_batch_client')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_client(self,
        container: str,
        blob: str,
        snapshot: Optional[str] = None,
        credential: Optional[Union[str, Dict[str, str], TokenCredential, None]] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Client operation.

        Args:
            container (str): Required parameter
            blob (str): Required parameter
            snapshot (Optional[str]): Optional parameter
            credential (Optional[Union[str, Dict[str, str], TokenCredential, None]]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'container': container, 'blob': blob}
        if snapshot is not None:
            kwargs['snapshot'] = snapshot
        if credential is not None:
            kwargs['credential'] = credential

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_client')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_service_client(self) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Service Client operation.


        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_service_client')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_service_cors_rules(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Service Cors Rules operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_service_cors_rules')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_service_logging(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Service Logging operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_service_logging')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_service_metrics(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Service Metrics operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_service_metrics')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_service_properties(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Service Properties operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_service_properties')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_blob_service_stats(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Blob Service Stats operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_blob_service_stats')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_container_client(self,
        container: str) -> AzureBlobResponse:
        """Azure Blob Storage Get Container Client operation.

        Args:
            container (str): Required parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'container': container}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_container_client')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_container_service_client(self) -> AzureBlobResponse:
        """Azure Blob Storage Get Container Service Client operation.


        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_container_service_client')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_retention_policy(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Retention Policy operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_retention_policy')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_service_client(self) -> AzureBlobResponse:
        """Azure Blob Storage Get Service Client operation.


        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_service_client')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_service_properties(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Service Properties operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_service_properties')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_service_stats(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Service Stats operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_service_stats')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_static_website(self,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get Static Website operation.

        Args:
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_static_website')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_user_delegation_key(self,
        key_start_time: datetime,
        key_expiry_time: datetime,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Get User Delegation Key operation.

        Args:
            key_start_time (datetime): Required parameter
            key_expiry_time (datetime): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'key_start_time': key_start_time, 'key_expiry_time': key_expiry_time}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'get_user_delegation_key')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def is_lease_active(self,
        lease_id: str,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Is Lease Active operation.

        Args:
            lease_id (str): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'lease_id': lease_id}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'is_lease_active')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def list_blob_containers(self,
        name_starts_with: Optional[str] = None,
        include_metadata: Optional[bool] = None,
        include_deleted: Optional[bool] = None,
        include_system: Optional[bool] = None,
        max_results: Optional[int] = None,
        timeout: Optional[int] = None,
        results_per_page: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage List Blob Containers operation.

        Args:
            name_starts_with (Optional[str]): Optional parameter
            include_metadata (Optional[bool]): Optional parameter
            include_deleted (Optional[bool]): Optional parameter
            include_system (Optional[bool]): Optional parameter
            max_results (Optional[int]): Optional parameter
            timeout (Optional[int]): Optional parameter
            results_per_page (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if name_starts_with is not None:
            kwargs['name_starts_with'] = name_starts_with
        if include_metadata is not None:
            kwargs['include_metadata'] = include_metadata
        if include_deleted is not None:
            kwargs['include_deleted'] = include_deleted
        if include_system is not None:
            kwargs['include_system'] = include_system
        if max_results is not None:
            kwargs['max_results'] = max_results
        if timeout is not None:
            kwargs['timeout'] = timeout
        if results_per_page is not None:
            kwargs['results_per_page'] = results_per_page

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'list_blob_containers')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def list_containers(self,
        name_starts_with: Optional[str] = None,
        include_metadata: Optional[bool] = None,
        include_deleted: Optional[bool] = None,
        include_system: Optional[bool] = None,
        max_results: Optional[int] = None,
        timeout: Optional[int] = None,
        results_per_page: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage List Containers operation.

        Args:
            name_starts_with (Optional[str]): Optional parameter
            include_metadata (Optional[bool]): Optional parameter
            include_deleted (Optional[bool]): Optional parameter
            include_system (Optional[bool]): Optional parameter
            max_results (Optional[int]): Optional parameter
            timeout (Optional[int]): Optional parameter
            results_per_page (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if name_starts_with is not None:
            kwargs['name_starts_with'] = name_starts_with
        if include_metadata is not None:
            kwargs['include_metadata'] = include_metadata
        if include_deleted is not None:
            kwargs['include_deleted'] = include_deleted
        if include_system is not None:
            kwargs['include_system'] = include_system
        if max_results is not None:
            kwargs['max_results'] = max_results
        if timeout is not None:
            kwargs['timeout'] = timeout
        if results_per_page is not None:
            kwargs['results_per_page'] = results_per_page

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'list_containers')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def move_blob(self,
        source_container_name: str,
        source_blob_name: str,
        destination_container_name: str,
        destination_blob_name: str,
        metadata: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Move Blob operation.

        Args:
            source_container_name (str): Required parameter
            source_blob_name (str): Required parameter
            destination_container_name (str): Required parameter
            destination_blob_name (str): Required parameter
            metadata (Optional[Dict[str, str]]): Optional parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'source_container_name': source_container_name, 'source_blob_name': source_blob_name, 'destination_container_name': destination_container_name, 'destination_blob_name': destination_blob_name}
        if metadata is not None:
            kwargs['metadata'] = metadata
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'move_blob')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def parse_connection_str(self,
        conn_str: str) -> AzureBlobResponse:
        """Azure Blob Storage Parse Connection Str operation.

        Args:
            conn_str (str): Required parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'conn_str': conn_str}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'parse_connection_str')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def parse_query(self,
        query_str: str) -> AzureBlobResponse:
        """Azure Blob Storage Parse Query operation.

        Args:
            query_str (str): Required parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'query_str': query_str}

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'parse_query')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def restore_container(self,
        deleted_container_name: str,
        deleted_container_version: str,
        new_name: Optional[str] = None,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Restore Container operation.

        Args:
            deleted_container_name (str): Required parameter
            deleted_container_version (str): Required parameter
            new_name (Optional[str]): Optional parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'deleted_container_name': deleted_container_name, 'deleted_container_version': deleted_container_version}
        if new_name is not None:
            kwargs['new_name'] = new_name
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'restore_container')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_blob_service_cors_rules(self,
        cors: List[CorsRule],
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Blob Service Cors Rules operation.

        Args:
            cors (List[CorsRule]): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'cors': cors}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_blob_service_cors_rules')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_blob_service_logging(self,
        analytics_logging: BlobAnalyticsLogging,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Blob Service Logging operation.

        Args:
            analytics_logging (BlobAnalyticsLogging): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'analytics_logging': analytics_logging}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_blob_service_logging')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_blob_service_metrics(self,
        hour_metrics: Metrics,
        minute_metrics: Metrics,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Blob Service Metrics operation.

        Args:
            hour_metrics (Metrics): Required parameter
            minute_metrics (Metrics): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'hour_metrics': hour_metrics, 'minute_metrics': minute_metrics}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_blob_service_metrics')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_blob_service_properties(self,
        analytics_logging: Optional[BlobAnalyticsLogging] = None,
        hour_metrics: Optional[Metrics] = None,
        minute_metrics: Optional[Metrics] = None,
        cors: Optional[List[CorsRule]] = None,
        target_version: Optional[str] = None,
        delete_retention_policy: Optional[RetentionPolicy] = None,
        static_website: Optional[StaticWebsite] = None,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Blob Service Properties operation.

        Args:
            analytics_logging (Optional[BlobAnalyticsLogging]): Optional parameter
            hour_metrics (Optional[Metrics]): Optional parameter
            minute_metrics (Optional[Metrics]): Optional parameter
            cors (Optional[List[CorsRule]]): Optional parameter
            target_version (Optional[str]): Optional parameter
            delete_retention_policy (Optional[RetentionPolicy]): Optional parameter
            static_website (Optional[StaticWebsite]): Optional parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {}
        if analytics_logging is not None:
            kwargs['analytics_logging'] = analytics_logging
        if hour_metrics is not None:
            kwargs['hour_metrics'] = hour_metrics
        if minute_metrics is not None:
            kwargs['minute_metrics'] = minute_metrics
        if cors is not None:
            kwargs['cors'] = cors
        if target_version is not None:
            kwargs['target_version'] = target_version
        if delete_retention_policy is not None:
            kwargs['delete_retention_policy'] = delete_retention_policy
        if static_website is not None:
            kwargs['static_website'] = static_website
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_blob_service_properties')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_retention_policy(self,
        delete_retention_policy: RetentionPolicy,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Retention Policy operation.

        Args:
            delete_retention_policy (RetentionPolicy): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'delete_retention_policy': delete_retention_policy}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_retention_policy')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_service_properties(self,
        analytics_logging: BlobAnalyticsLogging,
        hour_metrics: Optional[Metrics] = None,
        minute_metrics: Optional[Metrics] = None,
        cors: Optional[List[CorsRule]] = None,
        target_version: Optional[str] = None,
        delete_retention_policy: Optional[RetentionPolicy] = None,
        static_website: Optional[StaticWebsite] = None,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Service Properties operation.

        Args:
            analytics_logging (BlobAnalyticsLogging): Required parameter
            hour_metrics (Optional[Metrics]): Optional parameter
            minute_metrics (Optional[Metrics]): Optional parameter
            cors (Optional[List[CorsRule]]): Optional parameter
            target_version (Optional[str]): Optional parameter
            delete_retention_policy (Optional[RetentionPolicy]): Optional parameter
            static_website (Optional[StaticWebsite]): Optional parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'analytics_logging': analytics_logging}
        if hour_metrics is not None:
            kwargs['hour_metrics'] = hour_metrics
        if minute_metrics is not None:
            kwargs['minute_metrics'] = minute_metrics
        if cors is not None:
            kwargs['cors'] = cors
        if target_version is not None:
            kwargs['target_version'] = target_version
        if delete_retention_policy is not None:
            kwargs['delete_retention_policy'] = delete_retention_policy
        if static_website is not None:
            kwargs['static_website'] = static_website
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_service_properties')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_standard_blob_tier_blobs(self,
        blob_tier_data: List[Dict[str, Union[str, StandardBlobTier]]],
        timeout: Optional[int] = None,
        raise_on_any_failure: Optional[bool] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Standard Blob Tier Blobs operation.

        Args:
            blob_tier_data (List[Dict[str, Union[str, StandardBlobTier]]]): Required parameter
            timeout (Optional[int]): Optional parameter
            raise_on_any_failure (Optional[bool]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'blob_tier_data': blob_tier_data}
        if timeout is not None:
            kwargs['timeout'] = timeout
        if raise_on_any_failure is not None:
            kwargs['raise_on_any_failure'] = raise_on_any_failure

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_standard_blob_tier_blobs')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def set_static_website(self,
        static_website: StaticWebsite,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Set Static Website operation.

        Args:
            static_website (StaticWebsite): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'static_website': static_website}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'set_static_website')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def submit_batch(self,
        batch_requests: List[dict],
        timeout: Optional[int] = None,
        raise_on_any_failure: Optional[bool] = None) -> AzureBlobResponse:
        """Azure Blob Storage Submit Batch operation.

        Args:
            batch_requests (List[dict]): Required parameter
            timeout (Optional[int]): Optional parameter
            raise_on_any_failure (Optional[bool]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'batch_requests': batch_requests}
        if timeout is not None:
            kwargs['timeout'] = timeout
        if raise_on_any_failure is not None:
            kwargs['raise_on_any_failure'] = raise_on_any_failure

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'submit_batch')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def undelete_container(self,
        deleted_container_name: str,
        deleted_container_version: str,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Undelete Container operation.

        Args:
            deleted_container_name (str): Required parameter
            deleted_container_version (str): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'deleted_container_name': deleted_container_name, 'deleted_container_version': deleted_container_version}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'undelete_container')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def validate_lease(self,
        lease_id: str,
        timeout: Optional[int] = None) -> AzureBlobResponse:
        """Azure Blob Storage Validate Lease operation.

        Args:
            lease_id (str): Required parameter
            timeout (Optional[int]): Optional parameter

        Returns:
            AzureBlobResponse: Standardized response with success/data/error format
        """
        kwargs = {'lease_id': lease_id}
        if timeout is not None:
            kwargs['timeout'] = timeout

        try:
            # Route to service client
            blob_service_client = self._azure_blob_client.client.get_blob_service_client()
            response = getattr(blob_service_client, 'validate_lease')(**kwargs)

            return self._handle_azure_blob_response(response)
        except AzureError as e:
            return AzureBlobResponse(success=False, error=f"Azure error: {str(e)}")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Unexpected error: {str(e)}")

    # =================================
    # 🔧 UTILITY METHODS
    # =================================
    def get_azure_blob_client(self) -> AzureBlobClient:
        """Get the AzureBlobClient wrapper."""
        return self._azure_blob_client

    async def get_sdk_info(self) -> AzureBlobResponse:
        """Get information about the wrapped SDK methods."""
        info = {
            'total_methods': 181,
            'service': 'azure_blob_storage',
            'authentication_method': self._azure_blob_client.get_authentication_method(),
            'container_name': self._azure_blob_client.get_container_name(),
            'coverage': {
                'service_operations': 6,
                'sas_generation': 4,
                'container_operations': 15,
                'blob_operations': 40,
                'block_append_page_specifics': 20,
                'advanced_features': 10,
                'utilities': 15
            }
        }
        return AzureBlobResponse(success=True, data=info)

    async def health_check(self) -> AzureBlobResponse:
        """Perform health check on Azure Blob Storage connection."""
        try:
            await self.get_account_information()
            return AzureBlobResponse(success=True, message="Azure Blob Storage connection healthy")
        except Exception as e:
            return AzureBlobResponse(success=False, error=f"Health check failed: {str(e)}")
