from typing import Any, Dict, Optional

from app.sources.client.dropbox.dropbox import DropboxClient
from app.sources.client.http.http_response import HTTPResponse


class DropboxDataSource:
    def __init__(self, client: DropboxClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client.get_client()
        if self._client is None:
            raise ValueError("Dropbox client is not initialized.")
        try:
            self.base_url = self._client.get_base_url()
        except AttributeError as exc:
            raise AttributeError("Dropbox client missing 'get_base_url' method") from exc

    def get_data_source(self) -> 'DropboxDataSource':
        return self

    async def list_folder(
        self,
        path: str = "",
        recursive: bool = False,
        include_media_info: bool = False,
        include_deleted: bool = False,
        include_has_explicit_shared_members: bool = False,
        include_mounted_folders: bool = True,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: List Folder\n\nHTTP POST /2/files/list_folder"""
        if self._client is None:
            raise ValueError("Dropbox client is not initialized.")
        _headers: Dict[str, Any] = dict(headers or {})
        _body = {
            "path": path,
            "recursive": recursive,
            "include_media_info": include_media_info,
            "include_deleted": include_deleted,
            "include_has_explicit_shared_members": include_has_explicit_shared_members,
            "include_mounted_folders": include_mounted_folders,
        }
        if limit is not None:
            _body["limit"] = limit
        url = self.base_url + "/2/files/list_folder"
        return await self._client.request("POST", url, headers=_headers, json=_body)

    async def get_metadata(
        self,
        path: str,
        include_media_info: bool = False,
        include_deleted: bool = False,
        include_has_explicit_shared_members: bool = False,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Metadata\n\nHTTP POST /2/files/get_metadata"""
        if self._client is None:
            raise ValueError("Dropbox client is not initialized.")
        _headers: Dict[str, Any] = dict(headers or {})
        _body = {
            "path": path,
            "include_media_info": include_media_info,
            "include_deleted": include_deleted,
            "include_has_explicit_shared_members": include_has_explicit_shared_members,
        }
        url = self.base_url + "/2/files/get_metadata"
        return await self._client.request("POST", url, headers=_headers, json=_body)
