"""Instance-scoped SMB 2/3 client. Never uses the process-global session cache."""

from __future__ import annotations

import stat
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, BinaryIO

from app.connectors.core.constants import ConfigPaths
from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
from app.connectors.sources.network_share.errors import (
    DirectoryListingError,
    NetworkShareAuthError,
    ShareListingError,
)
from app.sources.client.iclient import IClient
from app.sources.client.smb.share_enum import enumerate_shares

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

REPARSE_POINT = 0x0400


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None


def unc(server: str, share: str, rel: str = "") -> str:
    rel = rel.replace("/", "\\").strip("\\")
    if rel:
        return rf"\\{server}\{share}\{rel}"
    return rf"\\{server}\{share}"


class SmbClient(IClient):
    def __init__(
        self,
        *,
        server: str,
        username: str,
        password: str,
        port: int = 445,
        domain: str = "",
        share: str | None = None,
        logger: Logger | None = None,
    ) -> None:
        self.server = server
        self.port = port
        self.username = f"{domain}\\{username}" if domain else username
        self.password = password
        self.default_share = share
        self.logger = logger
        self._connection_cache: dict[str, Any] = {}
        self._registered = False

    def get_client(self) -> dict[str, Any]:
        return self._connection_cache

    def connection_cache(self) -> dict[str, Any]:
        return self._connection_cache

    def _smbclient(self) -> object:
        try:
            import smbclient
        except ImportError as exc:
            raise NetworkShareAuthError(
                "smbprotocol is not installed. Add smbprotocol to the Python environment."
            ) from exc
        return smbclient

    def register(self) -> None:
        if self._registered:
            return
        smbclient = self._smbclient()
        smbclient.register_session(
            self.server,
            username=self.username,
            password=self.password,
            port=self.port,
            connection_cache=self._connection_cache,
        )
        self._registered = True

    def close(self) -> None:
        smbclient = self._smbclient()
        smbclient.reset_connection_cache(connection_cache=self._connection_cache)
        self._connection_cache.clear()
        self._registered = False

    def _kwargs(self) -> dict[str, Any]:
        return {"connection_cache": self._connection_cache, "port": self.port}

    def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        self.register()
        smbclient = self._smbclient()
        target = unc(self.server, share, path)
        entries: list[DirectoryEntry] = []
        try:
            with smbclient.scandir(target, **self._kwargs()) as scan:
                entries.extend(self._from_dir_entry(item) for item in scan)
        except FileNotFoundError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "no such" in message:
                raise FileNotFoundError(target) from exc
            if "logon" in message or "access denied" in message or "unsuccessful" in message:
                raise NetworkShareAuthError(str(exc)) from exc
            raise DirectoryListingError(share, path, str(exc)) from exc
        return entries

    def stat(self, share: str, path: str) -> DirectoryEntry | None:
        self.register()
        smbclient = self._smbclient()
        target = unc(self.server, share, path)
        try:
            result = smbclient.stat(target, **self._kwargs())
        except FileNotFoundError:
            return None
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "no such" in message:
                return None
            raise DirectoryListingError(share, path, str(exc)) from exc
        name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] if path else share
        is_dir = stat.S_ISDIR(result.st_mode)
        attrs = int(getattr(result, "st_file_attributes", 0) or 0)
        return DirectoryEntry(
            name=name,
            is_directory=is_dir,
            is_symlink=bool(attrs & REPARSE_POINT) or stat.S_ISLNK(result.st_mode),
            size=int(result.st_size or 0),
            created_time=_as_datetime(getattr(result, "st_ctime", None)),
            last_write_time=_as_datetime(getattr(result, "st_mtime", None)),
            file_id=int(result.st_ino) if getattr(result, "st_ino", None) else None,
        )

    def open_file(self, share: str, path: str) -> BinaryIO:
        self.register()
        smbclient = self._smbclient()
        target = unc(self.server, share, path)
        return smbclient.open_file(target, mode="rb", buffering=0, **self._kwargs())

    def list_shares(self) -> list[ShareInfo]:
        self.register()
        try:
            return enumerate_shares(self.server, self._connection_cache, port=self.port)
        except ShareListingError:
            if self.default_share:
                return [ShareInfo(name=self.default_share, share_type="disk")]
            raise

    def _from_dir_entry(self, item: object) -> DirectoryEntry:
        info = getattr(item, "smb_info", None)
        name = getattr(item, "name", "") or ""
        is_dir = bool(item.is_dir()) if callable(getattr(item, "is_dir", None)) else False
        is_link = bool(item.is_symlink()) if callable(getattr(item, "is_symlink", None)) else False
        inode = item.inode() if callable(getattr(item, "inode", None)) else None
        size = int(getattr(info, "end_of_file", 0) or 0) if info is not None else 0
        created = _as_datetime(getattr(info, "creation_time", None) if info is not None else None)
        written = _as_datetime(getattr(info, "last_write_time", None) if info is not None else None)
        attrs = int(getattr(info, "file_attributes", 0) or 0) if info is not None else 0
        if attrs & REPARSE_POINT:
            is_link = True
        file_id = int(inode) if inode else None
        return DirectoryEntry(
            name=name,
            is_directory=is_dir,
            is_symlink=is_link,
            size=size,
            created_time=created,
            last_write_time=written,
            file_id=file_id,
        )

    @classmethod
    async def build_from_services(
        cls,
        logger: Logger,
        config_service: ConfigurationService,
        connector_instance_id: str,
    ) -> "SmbClient":
        config = await config_service.get_config(
            ConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_instance_id)
        )
        if not config:
            raise NetworkShareAuthError("SMB configuration not found")
        auth = config.get("auth") or {}
        server = (auth.get("server") or "").strip()
        username = (auth.get("username") or "").strip()
        password = auth.get("password")
        if not server or not username or not password:
            raise NetworkShareAuthError("SMB server, username, and password are required")
        port = int(auth.get("port") or 445)
        domain = (auth.get("domain") or "").strip()
        share = (auth.get("share") or "").strip() or None
        client = cls(
            server=server,
            username=username,
            password=password,
            port=port,
            domain=domain,
            share=share,
            logger=logger,
        )
        client.register()
        return client
