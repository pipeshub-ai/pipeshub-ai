"""Azure Files storage SDK wrapper for connector integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from azure.core.exceptions import ResourceExistsError  # type: ignore[import-not-found]
from azure.storage.fileshare import (  # type: ignore[import-not-found]
    ShareDirectoryClient,
    ShareServiceClient,
)


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            yield path


class AzureFilesStorageHelper:
    """Wrapper around azure-storage-file-share for test usage."""

    def __init__(self, connection_string: str) -> None:
        self._service = ShareServiceClient.from_connection_string(connection_string)

    def create_share(self, share: str) -> None:
        self._service.create_share(share)

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

    def list_objects(self, share: str) -> List[str]:
        return list(self._iter_files_in_share(share))

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

    def upload_directory(self, share: str, root: Path) -> int:
        root = root.resolve()
        share_client = self._service.get_share_client(share)
        count = 0
        for file_path in _iter_files(root):
            rel_path = file_path.relative_to(root).as_posix()
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

    def clear_objects(self, share: str) -> None:
        share_client = self._service.get_share_client(share)
        for path in list(self._iter_files_in_share(share)):
            dir_name, _, file_name = path.rpartition("/")
            directory_client = (
                share_client.get_directory_client(dir_name)
                if dir_name
                else share_client.get_directory_client("")
            )
            file_client = directory_client.get_file_client(file_name)
            file_client.delete_file()

    def delete_share(self, share: str) -> None:
        self.clear_objects(share)
        self._service.get_share_client(share).delete_share()
