"""CIFS/SMB1 share helper for connector integration tests."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from smb import smb_structs

smb_structs.SUPPORT_SMB2 = False

from smb.SMBConnection import SMBConnection  # noqa: E402

from helper.run_folder import require_run_folder

CLIENT_NAME = "PIPESHUB"


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def _smb_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip("/")
    return f"/{cleaned}" if cleaned else "/"


class CifsStorageHelper:
    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        remote_name: str,
        *,
        port: int = 445,
        domain: str = "",
    ) -> None:
        self.server = server
        self.port = port
        self._conn = SMBConnection(
            username,
            password,
            CLIENT_NAME,
            remote_name,
            domain=domain,
            use_ntlm_v2=True,
            is_direct_tcp=port != 139,
        )
        if not self._conn.connect(server, port):
            raise ConnectionError("CIFS authentication failed")
        if getattr(self._conn, "isUsingSMB2", False):
            self._conn.close()
            raise ConnectionError("Server negotiated SMB 2+. Use the SMB integration suite.")

    def list_objects(self, share: str, prefix: str = "") -> list[str]:
        keys: list[str] = []

        def walk(path: str) -> None:
            try:
                entries = self._conn.listPath(share, _smb_path(path))
            except Exception:
                return
            for item in entries:
                name = getattr(item, "filename", "") or ""
                if name in (".", ".."):
                    continue
                rel = f"{path}/{name}" if path else name
                if getattr(item, "isDirectory", False):
                    walk(rel)
                else:
                    keys.append(rel.replace("\\", "/"))

        walk(prefix.replace("\\", "/").strip("/"))
        return keys

    def _ensure_dir(self, share: str, dir_name: str) -> None:
        parts = [p for p in dir_name.replace("\\", "/").split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            try:
                self._conn.createDirectory(share, _smb_path(current))
            except Exception:
                pass

    def upload_directory(self, share: str, root: Path, prefix: str = "") -> int:
        root = root.resolve()
        count = 0
        for file_path in _iter_files(root):
            rel = prefix + file_path.relative_to(root).as_posix()
            dir_name, _, _name = rel.rpartition("/")
            if dir_name:
                self._ensure_dir(share, dir_name)
            self._conn.storeFile(share, _smb_path(rel), BytesIO(file_path.read_bytes()))
            count += 1
        return count

    def rename_object(self, share: str, old_path: str, new_path: str) -> None:
        new_dir, _, _name = new_path.replace("\\", "/").rpartition("/")
        if new_dir:
            self._ensure_dir(share, new_dir)
        self._conn.rename(share, _smb_path(old_path), _smb_path(new_path))

    def clear_objects(self, share: str, prefix: str) -> None:
        folder = require_run_folder(prefix).rstrip("/")
        files = self.list_objects(share, prefix)
        for path in files:
            try:
                self._conn.deleteFiles(share, _smb_path(path))
            except Exception:
                pass
        dirs = {folder}
        for path in files:
            dir_name, _, _ = path.rpartition("/")
            parts = dir_name.split("/")
            dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        for directory in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
            if directory == folder or directory.startswith(folder + "/"):
                try:
                    self._conn.deleteDirectory(share, _smb_path(directory))
                except Exception:
                    pass
