
import asyncio

from dropbox.files import SearchOptions, WriteMode

from app.sources.client.dropbox.dropbox import DropboxClient


class DropboxDataSource:
    def __init__(self, client: DropboxClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client.get_client()
        if self._client is None:
            raise ValueError("Dropbox client is not initialized.")
        # Use Dropbox SDK client
        self._sdk = self._client.create_client()

    def get_data_source(self) -> 'DropboxDataSource':
        return self

    async def list_folder(self, path: str = "", recursive: bool = False, **kwargs) -> object:
        """List folder contents using Dropbox SDK."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._sdk.files_list_folder(path=path, recursive=recursive, **kwargs)
        )
        return result

    async def get_metadata(self, path: str, **kwargs) -> object:
        """Get file/folder metadata using Dropbox SDK."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._sdk.files_get_metadata(path, **kwargs)
        )
        return result

    async def upload(self, path: str, contents: bytes, mode: str = "add") -> object:
        """Upload a file to Dropbox using SDK."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._sdk.files_upload(contents, path, mode=WriteMode(mode))
        )
        return result

    async def download(self, path: str) -> object:
        """Download file from Dropbox using SDK."""
        loop = asyncio.get_running_loop()
        def _download() -> dict:
            metadata, res = self._sdk.files_download(path)
            data = res.content
            return {"metadata": metadata, "data": data}
        result = await loop.run_in_executor(None, _download)
        return result

    async def delete(self, path: str) -> object:
        """Delete a file or folder using SDK."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._sdk.files_delete_v2(path)
        )
        return result

    async def move(self, from_path: str, to_path: str) -> object:
        """Move or rename file/folder using SDK."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._sdk.files_move_v2(from_path, to_path)
        )
        return result

    async def copy(self, from_path: str, to_path: str) -> object:
        """Copy file/folder using SDK."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._sdk.files_copy_v2(from_path, to_path)
        )
        return result

    async def create_folder(self, path: str) -> object:
        """Create a new folder using SDK."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._sdk.files_create_folder_v2(path, autorename=False)
        )
        return result

    async def search(self, query: str, path: str = "", max_results: int = 10) -> object:
        """Search files/folders by name or metadata using SDK."""
        loop = asyncio.get_running_loop()
        def _search() -> object:
            options = None
            if path or max_results:
                options = SearchOptions(path=path, max_results=max_results)
            return self._sdk.files_search_v2(query, options=options)
        result = await loop.run_in_executor(None, _search)
        return result
