import json

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

    async def list_folder(self, path: str = "", recursive: bool = False, **kwargs) -> HTTPResponse:
        """List folder contents."""
        url = self.base_url + "/2/files/list_folder"
        _body = {"path": path, "recursive": recursive, **kwargs}
        return await self._client.request("POST", url, json=_body)

    async def get_metadata(self, path: str, **kwargs) -> HTTPResponse:
        """Get file/folder metadata."""
        url = self.base_url + "/2/files/get_metadata"
        _body = {"path": path, **kwargs}
        return await self._client.request("POST", url, json=_body)

    async def upload(self, path: str, contents: bytes, mode: str = "add") -> HTTPResponse:
        """Upload a file to Dropbox."""
        import aiohttp, json   # add json here
        url = "https://content.dropboxapi.com/2/files/upload"
        headers = {
            "Authorization": f"Bearer {self._client.access_token}",
            "Dropbox-API-Arg": json.dumps({"path": path, "mode": mode, "mute": False}),
            "Content-Type": "application/octet-stream",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=contents) as resp:
                return await resp.json()


    async def download(self, path: str) -> HTTPResponse:
        """Download file from Dropbox."""
        import aiohttp, json   # add json here
        url = "https://content.dropboxapi.com/2/files/download"
        headers = {
            "Authorization": f"Bearer {self._client.access_token}",
            "Dropbox-API-Arg": json.dumps({"path": path}),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers) as resp:
                data = await resp.read()
                return {"status": resp.status, "data": data}


    async def delete(self, path: str) -> HTTPResponse:
        """Delete a file or folder."""
        url = self.base_url + "/2/files/delete_v2"
        return await self._client.request("POST", url, json={"path": path})

    async def move(self, from_path: str, to_path: str) -> HTTPResponse:
        """Move or rename file/folder."""
        url = self.base_url + "/2/files/move_v2"
        return await self._client.request("POST", url, json={"from_path": from_path, "to_path": to_path})

    async def copy(self, from_path: str, to_path: str) -> HTTPResponse:
        """Copy file/folder."""
        url = self.base_url + "/2/files/copy_v2"
        return await self._client.request("POST", url, json={"from_path": from_path, "to_path": to_path})

    async def create_folder(self, path: str) -> HTTPResponse:
        """Create a new folder."""
        url = self.base_url + "/2/files/create_folder_v2"
        return await self._client.request("POST", url, json={"path": path, "autorename": False})

    async def search(self, query: str, path: str = "", max_results: int = 10) -> HTTPResponse:
        """Search files/folders by name or metadata."""
        url = self.base_url + "/2/files/search_v2"
        _body = {
            "query": query,
            "options": {"path": path, "max_results": max_results}
        }
        return await self._client.request("POST", url, json=_body)
