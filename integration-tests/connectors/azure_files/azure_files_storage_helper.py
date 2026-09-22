"""Azure Files storage SDK wrapper for connector integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from azure.core.exceptions import (  # type: ignore[import-not-found]
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.storage.fileshare import (  # type: ignore[import-not-found]
    ShareDirectoryClient,
    ShareServiceClient,
)


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            yield path


from helper.run_folder import require_run_folder

class AzureFilesStorageHelper:
    """Wrapper around azure-storage-file-share for test usage."""

    def __init__(self, connection_string: str) -> None:
        self._service = ShareServiceClient.from_connection_string(connection_string)

    def _iter_files_in_share(
        self, share: str, directory_path: str = ""
    ) -> Iterable[str]:
        share_client = self._service.get_share_client(share)
        directory: ShareDirectoryClient
        if directory_path:
            directory = share_client.get_directory_client(directory_path)
        else:
            directory = share_client.get_directory_client("")

        for item in directory.list_directories_and_files():
            name = item["name"]
            path = f"{directory_path}/{name}" if directory_path else name
            if item["is_directory"]:
                yield from self._iter_files_in_share(share, path)
            else:
                yield path

    def list_objects(self, share: str, prefix: str = "") -> List[str]:
        folder = prefix.rstrip("/")
        try:
            return list(self._iter_files_in_share(share, folder))
        except ResourceNotFoundError:
            if not folder:
                raise
            # A missing share is the same 404 as a missing folder: raise if it is the share.
            self._service.get_share_client(share).get_share_properties()
            return []  # this run's folder has not been created yet

    def _ensure_azure_files_directory(self, share_client: object, dir_name: str) -> object:
        if not dir_name:
            return share_client.get_directory_client("")  # type: ignore[return-value]
        current = share_client.get_directory_client("")
        for part in dir_name.split("/"):
            if not part:
                continue
            current = current.get_subdirectory_client(part)
            try:
                current.create_directory()
            except ResourceExistsError:
                pass
        return share_client.get_directory_client(dir_name)

    def upload_directory(self, share: str, root: Path, prefix: str = "") -> int:
        root = root.resolve()
        share_client = self._service.get_share_client(share)
        count = 0
        for file_path in _iter_files(root):
            rel_path = prefix + file_path.relative_to(root).as_posix()
            dir_name, _, file_name = rel_path.rpartition("/")

            if dir_name:
                directory_client = self._ensure_azure_files_directory(share_client, dir_name)
            else:
                directory_client = share_client.get_directory_client("")

            file_client = directory_client.get_file_client(file_name)
            data = file_path.read_bytes()
            file_client.upload_file(data)
            count += 1

        return count

    def upload_file(self, share: str, key: str, data: bytes) -> None:
        share_client = self._service.get_share_client(share)
        dir_name, _, file_name = key.rpartition("/")
        if dir_name:
            directory_client = self._ensure_azure_files_directory(share_client, dir_name)
        else:
            directory_client = share_client.get_directory_client("")
        file_client = directory_client.get_file_client(file_name)
        file_client.upload_file(data)

    def overwrite_file(self, share: str, key: str, data: bytes) -> None:
        share_client = self._service.get_share_client(share)
        dir_name, _, file_name = key.rpartition("/")
        if dir_name:
            directory_client = share_client.get_directory_client(dir_name)
        else:
            directory_client = share_client.get_directory_client("")
        file_client = directory_client.get_file_client(file_name)
        file_client.upload_file(data)

    def get_file_metadata(self, share: str, key: str) -> dict:
        share_client = self._service.get_share_client(share)
        dir_name, _, file_name = key.rpartition("/")
        if dir_name:
            directory_client = share_client.get_directory_client(dir_name)
        else:
            directory_client = share_client.get_directory_client("")
        file_client = directory_client.get_file_client(file_name)
        props = file_client.get_file_properties()
        return {
            "etag": props.etag,
            "last_modified": props.last_modified,
            "size": props.size,
        }

    def rename_object(self, share: str, old_path: str, new_path: str) -> None:
        self._rename_within_share(share, old_path, new_path)

    def move_object(self, share: str, old_path: str, new_path: str) -> None:
        self._rename_within_share(share, old_path, new_path)

    def _rename_within_share(self, share: str, old_path: str, new_path: str) -> None:
        share_client = self._service.get_share_client(share)
        old_dir, _, old_name = old_path.rpartition("/")
        new_dir, _, _new_name = new_path.rpartition("/")

        if new_dir:
            self._ensure_azure_files_directory(share_client, new_dir)

        src_dir_client = (
            share_client.get_directory_client(old_dir)
            if old_dir
            else share_client.get_directory_client("")  # type: ignore[call-arg]
        )
        src_file_client = src_dir_client.get_file_client(old_name)
        dest_path = new_path.strip("/")
        src_file_client.rename_file(dest_path, overwrite=True)

    def clear_objects(self, share: str, prefix: str) -> None:
        """Delete this run's folder and everything in it, and nothing else."""
        folder = require_run_folder(prefix).rstrip("/")
        share_client = self._service.get_share_client(share)
        directories = {folder}
        for path in self.list_objects(share, prefix):
            dir_name, _, file_name = path.rpartition("/")
            parts = dir_name.split("/")
            directories.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
            share_client.get_directory_client(dir_name).get_file_client(file_name).delete_file()
        # Deepest first: a directory can only be deleted once it is empty.
        for directory in sorted(directories, key=lambda d: d.count("/"), reverse=True):
            if directory == folder or directory.startswith(folder + "/"):
                try:
                    share_client.get_directory_client(directory).delete_directory()
                except HttpResponseError:
                    pass  # already gone, or not empty; a leftover empty folder is harmless
