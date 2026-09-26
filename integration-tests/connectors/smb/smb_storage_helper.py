"""SMB share SDK wrapper for connector integration tests."""

from __future__ import annotations

from pathlib import Path

import smbclient

from helper.run_folder import require_run_folder


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            yield path


class SmbStorageHelper:
    """smbprotocol-backed helper. Uses an instance connection cache."""

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        *,
        port: int = 445,
        domain: str = "",
    ) -> None:
        self.server = server
        self.port = port
        self._cache: dict = {}
        user = f"{domain}\\{username}" if domain else username
        smbclient.register_session(
            server,
            username=user,
            password=password,
            port=port,
            connection_cache=self._cache,
        )

    def _unc(self, share: str, rel: str = "") -> str:
        rel = rel.replace("/", "\\").strip("\\")
        if rel:
            return rf"\\{self.server}\{share}\{rel}"
        return rf"\\{self.server}\{share}"

    def _kwargs(self) -> dict:
        return {"connection_cache": self._cache, "port": self.port}

    def list_objects(self, share: str, prefix: str = "") -> list[str]:
        keys: list[str] = []

        def walk(path: str) -> None:
            try:
                with smbclient.scandir(self._unc(share, path), **self._kwargs()) as scan:
                    for item in scan:
                        rel = f"{path}/{item.name}" if path else item.name
                        if item.is_dir():
                            walk(rel)
                        else:
                            keys.append(rel.replace("\\", "/"))
            except FileNotFoundError:
                return

        walk(prefix.replace("\\", "/").strip("/"))
        return keys

    def _ensure_dir(self, share: str, dir_name: str) -> None:
        if not dir_name:
            return
        parts = [p for p in dir_name.replace("\\", "/").split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            target = self._unc(share, current)
            try:
                smbclient.makedirs(target, exist_ok=True, **self._kwargs())
            except OSError:
                pass

    def upload_directory(self, share: str, root: Path, prefix: str = "") -> int:
        root = root.resolve()
        count = 0
        for file_path in _iter_files(root):
            rel = prefix + file_path.relative_to(root).as_posix()
            dir_name, _, _name = rel.rpartition("/")
            if dir_name:
                self._ensure_dir(share, dir_name)
            with smbclient.open_file(self._unc(share, rel), mode="wb", **self._kwargs()) as handle:
                handle.write(file_path.read_bytes())
            count += 1
        return count

    def rename_object(self, share: str, old_path: str, new_path: str) -> None:
        new_dir, _, _name = new_path.replace("\\", "/").rpartition("/")
        if new_dir:
            self._ensure_dir(share, new_dir)
        smbclient.rename(self._unc(share, old_path), self._unc(share, new_path), **self._kwargs())

    def clear_objects(self, share: str, prefix: str) -> None:
        folder = require_run_folder(prefix).rstrip("/")
        files = self.list_objects(share, prefix)
        for path in files:
            try:
                smbclient.remove(self._unc(share, path), **self._kwargs())
            except OSError:
                pass
        dirs = {folder}
        for path in files:
            dir_name, _, _ = path.rpartition("/")
            parts = dir_name.split("/")
            dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        for directory in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
            if directory == folder or directory.startswith(folder + "/"):
                try:
                    smbclient.rmdir(self._unc(share, directory), **self._kwargs())
                except OSError:
                    pass
