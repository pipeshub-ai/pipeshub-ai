"""
Azure Files Data Source

Provides a data source class for interacting with Azure File Shares API.
Supports listing shares, directories, files, and generating SAS URLs.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from azure.core.exceptions import AzureError, ResourceNotFoundError  # type: ignore
    from azure.storage.fileshare import (  # type: ignore
        FileSasPermissions,
        ShareSasPermissions,
        generate_file_sas,
        generate_share_sas,
    )
    from azure.storage.fileshare.aio import (  # type: ignore
        ShareServiceClient as AsyncShareServiceClient,
    )
except ImportError:
    raise ImportError(
        "azure-storage-file-share is not installed. "
        "Please install it with `pip install azure-storage-file-share`"
    )

from app.sources.client.azure.azure_files import AzureFilesClient, AzureFilesResponse

# Constants
PATH_PARTS_WITH_DIRECTORY = 2  # Number of parts when path contains directory and filename


class AzureFilesDataSource:
    """
    Azure Files Data Source - API wrapper for Azure File Share operations.

    Provides methods for:
    - Listing file shares
    - Listing directories and files (with recursive support)
    - Getting file/directory properties
    - Generating SAS URLs for file access
    """

    def __init__(self, azure_files_client: AzureFilesClient) -> None:
        """Initialize with AzureFilesClient."""
        self._azure_files_client = azure_files_client
        self._async_share_service_client: Optional[AsyncShareServiceClient] = None

    async def _get_async_share_service_client(self) -> AsyncShareServiceClient:
        """Get the async share service client, creating it if needed."""
        if self._async_share_service_client is None:
            self._async_share_service_client = (
                await self._azure_files_client.get_async_share_service_client()
            )
        return self._async_share_service_client

    def _handle_response(
        self,
        data: object = None,
        error: Optional[str] = None,
        message: Optional[str] = None,
    ) -> AzureFilesResponse:
        """Create a standardized response."""
        if error:
            return AzureFilesResponse(success=False, error=error, message=message)
        return AzureFilesResponse(success=True, data=data, message=message)

    async def list_shares(
        self,
        name_starts_with: Optional[str] = None,
        include_metadata: bool = False,
        include_snapshots: bool = False,
    ) -> AzureFilesResponse:
        """List all file shares in the storage account.

        Args:
            name_starts_with: Filter shares by prefix
            include_metadata: Include share metadata in response
            include_snapshots: Include share snapshots in response

        Returns:
            AzureFilesResponse with list of share information
        """
        try:
            client = await self._get_async_share_service_client()

            include_list = []
            if include_metadata:
                include_list.append("metadata")
            if include_snapshots:
                include_list.append("snapshots")

            shares = []
            async for share in client.list_shares(
                name_starts_with=name_starts_with,
                include_metadata=include_metadata,
                include_snapshots=include_snapshots,
            ):
                share_info = {
                    "name": share.name,
                    "last_modified": share.last_modified,
                    "quota": share.quota,
                    "metadata": share.metadata if include_metadata else None,
                    "snapshot": share.snapshot if include_snapshots else None,
                }
                shares.append(share_info)

            return self._handle_response(data=shares)

        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def list_directories_and_files(
        self,
        share_name: str,
        directory_path: str = "",
        name_starts_with: Optional[str] = None,
    ) -> AzureFilesResponse:
        """List directories and files in a share directory.

        Args:
            share_name: Name of the file share
            directory_path: Path to the directory (empty string for root)
            name_starts_with: Filter items by prefix

        Returns:
            AzureFilesResponse with list of directory/file information
        """
        try:
            client = await self._get_async_share_service_client()
            share_client = client.get_share_client(share_name)
            directory_client = share_client.get_directory_client(directory_path)

            items: List[Dict[str, Any]] = []
            async for item in directory_client.list_directories_and_files(
                name_starts_with=name_starts_with
            ):
                item_info = {
                    "name": item.name,
                    "is_directory": item.is_directory,
                    "size": getattr(item, "size", None),
                    "last_modified": getattr(item, "last_modified", None),
                    "etag": getattr(item, "etag", None),
                    "content_length": getattr(item, "content_length", None),
                    # Build full path
                    "path": f"{directory_path}/{item.name}".lstrip("/")
                    if directory_path
                    else item.name,
                    "parent_path": directory_path or None,
                }
                items.append(item_info)

            return self._handle_response(data=items)

        except ResourceNotFoundError:
            return self._handle_response(
                error=f"Directory not found: {share_name}/{directory_path}"
            )
        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def get_file_properties(
        self, share_name: str, file_path: str
    ) -> AzureFilesResponse:
        """Get properties of a file.

        Args:
            share_name: Name of the file share
            file_path: Full path to the file within the share

        Returns:
            AzureFilesResponse with file properties
        """
        try:
            client = await self._get_async_share_service_client()
            share_client = client.get_share_client(share_name)
            file_client = share_client.get_file_client(file_path)

            properties = await file_client.get_file_properties()

            file_props = {
                "name": properties.name,
                "path": file_path,
                "size": properties.size,
                "content_type": properties.content_settings.content_type
                if properties.content_settings
                else None,
                "content_md5": properties.content_settings.content_md5
                if properties.content_settings
                else None,
                "etag": properties.etag,
                "last_modified": properties.last_modified,
                "creation_time": properties.creation_time,
                "last_write_time": properties.last_write_time,
                "last_access_time": properties.last_access_time,
                "metadata": properties.metadata,
                "is_directory": False,
            }

            return self._handle_response(data=file_props)

        except ResourceNotFoundError:
            return self._handle_response(
                error=f"File not found: {share_name}/{file_path}"
            )
        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def get_directory_properties(
        self, share_name: str, directory_path: str
    ) -> AzureFilesResponse:
        """Get properties of a directory.

        Args:
            share_name: Name of the file share
            directory_path: Full path to the directory within the share

        Returns:
            AzureFilesResponse with directory properties
        """
        try:
            client = await self._get_async_share_service_client()
            share_client = client.get_share_client(share_name)
            directory_client = share_client.get_directory_client(directory_path)

            properties = await directory_client.get_directory_properties()

            dir_props = {
                "name": properties.name,
                "path": directory_path,
                "etag": properties.etag,
                "last_modified": properties.last_modified,
                "creation_time": properties.creation_time,
                "last_write_time": properties.last_write_time,
                "metadata": properties.metadata,
                "is_directory": True,
            }

            return self._handle_response(data=dir_props)

        except ResourceNotFoundError:
            return self._handle_response(
                error=f"Directory not found: {share_name}/{directory_path}"
            )
        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def get_share_properties(self, share_name: str) -> AzureFilesResponse:
        """Get properties of a file share.

        Args:
            share_name: Name of the file share

        Returns:
            AzureFilesResponse with share properties
        """
        try:
            client = await self._get_async_share_service_client()
            share_client = client.get_share_client(share_name)

            properties = await share_client.get_share_properties()

            share_props = {
                "name": share_name,
                "quota": properties.quota,
                "etag": properties.etag,
                "last_modified": properties.last_modified,
                "metadata": properties.metadata,
                "access_tier": properties.access_tier,
                "protocols": properties.protocols,
            }

            return self._handle_response(data=share_props)

        except ResourceNotFoundError:
            return self._handle_response(error=f"Share not found: {share_name}")
        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def generate_file_sas_url(
        self,
        share_name: str,
        file_path: str,
        permission: str = "r",
        expiry: Optional[datetime] = None,
        start: Optional[datetime] = None,
    ) -> AzureFilesResponse:
        """Generate a SAS URL for a file.

        Args:
            share_name: Name of the file share
            file_path: Full path to the file within the share
            permission: SAS permissions (r=read, default: "r")
            expiry: Expiry time for the SAS token (default: 24 hours from now)
            start: Start time for the SAS token (default: now)

        Returns:
            AzureFilesResponse with SAS URL
        """
        try:
            account_name = self._azure_files_client.get_account_name()
            account_key = self._azure_files_client.get_account_key()

            if not account_key:
                return self._handle_response(
                    error="Account key required for SAS URL generation"
                )

            if expiry is None:
                expiry = datetime.now(timezone.utc) + timedelta(hours=24)

            if start is None:
                start = datetime.now(timezone.utc) - timedelta(minutes=5)

            # Parse file path to get directory and filename
            path_parts = file_path.rsplit("/", 1)
            if len(path_parts) == PATH_PARTS_WITH_DIRECTORY:
                directory_path = path_parts[0]
                file_name = path_parts[1]
            else:
                directory_path = ""
                file_name = file_path

            sas_token = generate_file_sas(
                account_name=account_name,
                share_name=share_name,
                directory_name=directory_path,
                file_name=file_name,
                account_key=account_key,
                permission=FileSasPermissions(read="r" in permission),
                expiry=expiry,
                start=start,
            )

            # Construct the full URL
            account_url = self._azure_files_client.get_account_url()
            sas_url = f"{account_url}/{share_name}/{file_path}?{sas_token}"

            return self._handle_response(
                data={"sas_url": sas_url, "expiry": expiry.isoformat()}
            )

        except AzureError as e:
            return self._handle_response(
                error=f"Azure Files API error generating SAS: {str(e)}"
            )
        except Exception as e:
            return self._handle_response(
                error=f"Unexpected error generating SAS: {str(e)}"
            )

    async def generate_share_sas_url(
        self,
        share_name: str,
        permission: str = "rl",
        expiry: Optional[datetime] = None,
        start: Optional[datetime] = None,
    ) -> AzureFilesResponse:
        """Generate a SAS URL for a share.

        Args:
            share_name: Name of the file share
            permission: SAS permissions (r=read, l=list, default: "rl")
            expiry: Expiry time for the SAS token (default: 24 hours from now)
            start: Start time for the SAS token (default: now)

        Returns:
            AzureFilesResponse with SAS URL
        """
        try:
            account_name = self._azure_files_client.get_account_name()
            account_key = self._azure_files_client.get_account_key()

            if not account_key:
                return self._handle_response(
                    error="Account key required for SAS URL generation"
                )

            if expiry is None:
                expiry = datetime.now(timezone.utc) + timedelta(hours=24)

            if start is None:
                start = datetime.now(timezone.utc) - timedelta(minutes=5)

            sas_token = generate_share_sas(
                account_name=account_name,
                share_name=share_name,
                account_key=account_key,
                permission=ShareSasPermissions(
                    read="r" in permission, list="l" in permission
                ),
                expiry=expiry,
                start=start,
            )

            # Construct the full URL
            account_url = self._azure_files_client.get_account_url()
            sas_url = f"{account_url}/{share_name}?{sas_token}"

            return self._handle_response(
                data={"sas_url": sas_url, "expiry": expiry.isoformat()}
            )

        except AzureError as e:
            return self._handle_response(
                error=f"Azure Files API error generating SAS: {str(e)}"
            )
        except Exception as e:
            return self._handle_response(
                error=f"Unexpected error generating SAS: {str(e)}"
            )

    async def download_file(
        self,
        share_name: str,
        file_path: str,
        offset: int = 0,
        length: Optional[int] = None,
    ) -> AzureFilesResponse:
        """Download a file's content.

        Args:
            share_name: Name of the file share
            file_path: Full path to the file within the share
            offset: Byte offset to start reading from
            length: Number of bytes to read (None for entire file)

        Returns:
            AzureFilesResponse with file content as bytes
        """
        try:
            client = await self._get_async_share_service_client()
            share_client = client.get_share_client(share_name)
            file_client = share_client.get_file_client(file_path)

            download = await file_client.download_file(offset=offset, length=length)
            content = await download.readall()

            return self._handle_response(
                data={
                    "content": content,
                    "size": len(content),
                    "file_path": file_path,
                }
            )

        except ResourceNotFoundError:
            return self._handle_response(
                error=f"File not found: {share_name}/{file_path}"
            )
        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def check_directory_exists(
        self, share_name: str, directory_path: str
    ) -> AzureFilesResponse:
        """Check if a directory exists.

        Args:
            share_name: Name of the file share
            directory_path: Full path to the directory within the share

        Returns:
            AzureFilesResponse with exists boolean
        """
        try:
            client = await self._get_async_share_service_client()
            share_client = client.get_share_client(share_name)
            directory_client = share_client.get_directory_client(directory_path)

            exists = await directory_client.exists()

            return self._handle_response(data={"exists": exists, "path": directory_path})

        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def check_file_exists(
        self, share_name: str, file_path: str
    ) -> AzureFilesResponse:
        """Check if a file exists.

        Args:
            share_name: Name of the file share
            file_path: Full path to the file within the share

        Returns:
            AzureFilesResponse with exists boolean
        """
        try:
            client = await self._get_async_share_service_client()
            share_client = client.get_share_client(share_name)
            file_client = share_client.get_file_client(file_path)

            exists = await file_client.exists()

            return self._handle_response(data={"exists": exists, "path": file_path})

        except AzureError as e:
            return self._handle_response(error=f"Azure Files API error: {str(e)}")
        except Exception as e:
            return self._handle_response(error=f"Unexpected error: {str(e)}")

    async def close_async_client(self) -> None:
        """Close the async share service client."""
        if self._async_share_service_client:
            await self._async_share_service_client.close()
            self._async_share_service_client = None
        await self._azure_files_client.close_async_client()
