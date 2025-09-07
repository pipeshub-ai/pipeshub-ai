from typing import Any, Dict, Optional

from app.sources.client.box.box import BoxClient, BoxRESTClientViaAccessToken
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse


class BoxDataSource:
    def __init__(self, client: BoxClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client.get_client()
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self._client.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'BoxDataSource':
        return self

    async def get_user_info(self, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        """Example: Get current user's info from Box API (GET /users/me)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Authorization'] = f"Bearer {self._client.access_token}"
        url = self.base_url + '/users/me'
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=None,
        )
        resp = await self._client.execute(req)
        return resp

    async def list_folder_items(self, folder_id: str = '0', headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        """List items in a folder (GET /folders/:folder_id/items)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Authorization'] = f"Bearer {self._client.access_token}"
        url = f"{self.base_url}/folders/{folder_id}/items"
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=None,
        )
        resp = await self._client.execute(req)
        return resp

    async def upload_file(self, folder_id: str, file_name: str, file_content: bytes, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        """Upload a file to a folder (POST /files/content)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Authorization'] = f"Bearer {self._client.access_token}"
        url = f"{self.base_url}/files/content"
        # Box API expects multipart/form-data for file uploads
        # This is a stub; actual implementation may require a different HTTP client
        body = {
            'attributes': {'name': file_name, 'parent': {'id': folder_id}},
            'file': file_content
        }
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=body,
        )
        resp = await self._client.execute(req)
        return resp

    async def download_file(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        """Download a file (GET /files/:file_id/content)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Authorization'] = f"Bearer {self._client.access_token}"
        url = f"{self.base_url}/files/{file_id}/content"
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=None,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_file(self, file_id: str, headers: Optional[Dict[str, Any]] = None) -> HTTPResponse:
        """Delete a file (DELETE /files/:file_id)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Authorization'] = f"Bearer {self._client.access_token}"
        url = f"{self.base_url}/files/{file_id}"
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=None,
        )
        resp = await self._client.execute(req)
        return resp
