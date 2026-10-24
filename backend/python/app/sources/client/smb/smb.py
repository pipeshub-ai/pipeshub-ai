"""Instance-scoped SMB 2/3 client. Never uses the process-global session cache."""

from __future__ import annotations

import asyncio
import ipaddress
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, BinaryIO, TypeVar

from app.connectors.core.constants import ConfigPaths
from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
from app.connectors.sources.network_share.errors import (
    DirectoryListingError,
    NetworkShareAuthError,
    ShareListingError,
)
from app.sources.client.iclient import IClient
from app.sources.client.smb.share_enum import enumerate_shares
from app.utils.url_fetcher import (
    HostCheckError,
    check_addresses,
    literal_ip,
    resolved_addresses,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger
    from types import ModuleType

    from app.config.configuration_service import ConfigurationService

REPARSE_POINT = 0x0400
# smbclient waits 60 s by default, longer than a gateway holds a request open.
CONNECTION_TIMEOUT_SECONDS = 15
LOOKUP_TIMEOUT_SECONDS = 5
# getaddrinfo has no timeout. A lookup left running here does not hold a client's lock.
_lookups = ThreadPoolExecutor(max_workers=4, thread_name_prefix="smb-lookup")
_T = TypeVar("_T")


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


def _stat_time(result: object, field: str) -> datetime | None:
    """A stat time equal to the one a directory listing gives for the same file.

    ``st_mtime`` is float seconds and rounds to a neighbouring microsecond for
    about half of all files, which changes the revision of an unchanged file.
    """
    nanos = getattr(result, f"{field}_ns", None)
    if isinstance(nanos, int):
        return datetime.fromtimestamp(0, tz=timezone.utc) + timedelta(microseconds=nanos // 1000)
    return _as_datetime(getattr(result, field, None))


def dial_address(server: str) -> str:
    """The address to connect to for ``server``, looked up once.

    Every path is then built on this address, so a second lookup cannot move the
    connection. The deployment's rule for hosts users type applies (``check_addresses``:
    link-local and cloud metadata never, private ones not while
    PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on), and loopback, unspecified and multicast
    addresses are never a file server either. Sessions authenticate with NTLM (the image
    has no Kerberos), which does not need the server's name.

    Raises:
        NetworkShareAuthError: the name doesn't resolve or an address is refused.
    """
    literal = literal_ip(server)
    try:
        addresses = (
            [literal]
            if literal is not None
            else _lookups.submit(resolved_addresses, server).result(timeout=LOOKUP_TIMEOUT_SECONDS)
        )
    except TimeoutError as exc:
        raise NetworkShareAuthError(f'Looking up "{server}" timed out. Check the server name.') from exc
    if not addresses:
        raise NetworkShareAuthError(f'Could not find a server named "{server}". Check the server name.')
    plain = [
        a.ipv4_mapped if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None else a
        for a in addresses
    ]
    if any(a.is_loopback or a.is_unspecified or a.is_multicast for a in plain):
        raise NetworkShareAuthError(
            f"The SMB server address {server} is not allowed: loopback, link-local and multicast "
            "addresses cannot be used."
        )
    try:
        check_addresses(server, addresses)
    except HostCheckError as exc:
        raise NetworkShareAuthError(str(exc)) from exc
    return str(next((a for a in plain if a.version == 4), plain[0]))


def _vet_every_session(pool: ModuleType) -> None:
    """Send every session smbclient opens through ``dial_address``.

    A DFS referral makes smbclient open a session to a server the user never typed
    (``get_smb_tree`` and ``_resolve_dfs`` call ``_pool.register_session``).
    smbprotocol is pinned, so the private name is stable.
    """
    original = pool.register_session
    if getattr(original, "vetted", False) is True:
        return

    def register_session(server: str, *args: object, **kwargs: object) -> object:
        return original(dial_address(server), *args, **kwargs)

    register_session.vetted = True  # type: ignore[attr-defined]
    pool.register_session = register_session


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
        # smbclient takes an IPv6 address without the brackets a URL would use.
        self.server = server.removeprefix("[").removesuffix("]")
        # The address actually dialled, set when the session is registered.
        self.host = self.server
        self.port = port
        self.username = f"{domain}\\{username}" if domain else username
        self.password = password
        self.default_share = share
        self.logger = logger
        self._connection_cache: dict[str, Any] = {}
        self._registered = False
        # smbprotocol does not wait for SMB credits. Requests from several
        # threads on one connection fail with "requires 1 credits but only 0
        # credits are available" and can leave the connection at zero for good.
        self._io_lock = threading.RLock()

    def get_client(self) -> dict[str, Any]:
        return self._connection_cache

    def connection_cache(self) -> dict[str, Any]:
        return self._connection_cache

    def _smbclient(self) -> object:
        try:
            import smbclient
            from smbclient import _pool
        except ImportError as exc:
            raise NetworkShareAuthError(
                "smbprotocol is not installed. Add smbprotocol to the Python environment."
            ) from exc
        _vet_every_session(_pool)
        return smbclient

    def serialized(self, fn: Callable[..., _T], *args: object) -> _T:
        """Run one call on this connection at a time. Handles from open_file go through here too."""
        with self._io_lock:
            return fn(*args)

    def register(self) -> None:
        with self._io_lock:
            if self._registered:
                return
            self.host = dial_address(self.server)
            smbclient = self._smbclient()
            smbclient.register_session(
                self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                connection_timeout=CONNECTION_TIMEOUT_SECONDS,
                connection_cache=self._connection_cache,
            )
            self._registered = True

    def close(self) -> None:
        with self._io_lock:
            smbclient = self._smbclient()
            smbclient.reset_connection_cache(connection_cache=self._connection_cache)
            self._connection_cache.clear()
            self._registered = False

    def _kwargs(self) -> dict[str, Any]:
        # smbprotocol keeps credentials only inside the live session. After the
        # server drops the connection it opens a new one from these arguments.
        return {
            "connection_cache": self._connection_cache,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "connection_timeout": CONNECTION_TIMEOUT_SECONDS,
        }

    def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        with self._io_lock:
            return self._list_directory(share, path)

    def _list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        self.register()
        smbclient = self._smbclient()
        target = unc(self.host, share, path)
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

    def stat(self, share: str, path: str, *, follow: bool = True) -> DirectoryEntry | None:
        with self._io_lock:
            return self._stat(share, path, follow=follow)

    def _stat(self, share: str, path: str, *, follow: bool = True) -> DirectoryEntry | None:
        self.register()
        smbclient = self._smbclient()
        target = unc(self.host, share, path)
        try:
            result = smbclient.stat(target, follow_symlinks=follow, **self._kwargs())
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
            is_symlink=stat.S_ISLNK(result.st_mode),
            is_reparse=bool(attrs & REPARSE_POINT),
            size=int(result.st_size or 0),
            created_time=_stat_time(result, "st_ctime"),
            last_write_time=_stat_time(result, "st_mtime"),
            file_id=int(result.st_ino) if getattr(result, "st_ino", None) else None,
            attributes=attrs,
        )

    def open_file(self, share: str, path: str) -> BinaryIO:
        with self._io_lock:
            self.register()
            smbclient = self._smbclient()
            target = unc(self.host, share, path)
            # The default share mode for "rb" is read-only, so two overlapping
            # previews of one file fail with STATUS_SHARING_VIOLATION.
            return smbclient.open_file(
                target, mode="rb", buffering=0, share_access="rwd", **self._kwargs()
            )

    def list_shares(self) -> list[ShareInfo]:
        with self._io_lock:
            self.register()
            try:
                return enumerate_shares(self.host, self._connection_cache, port=self.port)
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
        file_id = int(inode) if inode else None
        return DirectoryEntry(
            name=name,
            is_directory=is_dir,
            is_symlink=is_link,
            is_reparse=bool(attrs & REPARSE_POINT),
            size=size,
            created_time=created,
            last_write_time=written,
            file_id=file_id,
            attributes=attrs,
        )

    @classmethod
    async def build_from_services(
        cls,
        logger: Logger,
        config_service: ConfigurationService,
        connector_instance_id: str,
    ) -> "SmbClient":
        config = await config_service.get_config(
            ConfigPaths.CONNECTOR_CONFIG_PATH.format(connector_id=connector_instance_id)
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
        await asyncio.to_thread(client.register)
        return client
