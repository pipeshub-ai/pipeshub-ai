"""SMB1/CIFS client. pysmb is forced to SMB1 for the life of this process.

``smb.smb_structs.SUPPORT_SMB2`` is process-global. This module sets it False
before constructing any connection. The SMB 2/3 connector uses smbprotocol and
must not import this package.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING, TypeVar

from smb import smb_structs

from app.connectors.core.constants import ConfigPaths
from app.connectors.sources.network_share.entry import DirectoryEntry, ShareInfo
from app.connectors.sources.network_share.errors import (
    DialectError,
    DirectoryListingError,
    NetworkShareAuthError,
    ShareListingError,
)
from app.sources.client.iclient import IClient

smb_structs.SUPPORT_SMB2 = False

from smb.base import NotConnectedError  # noqa: E402
from smb.SMBConnection import SMBConnection  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

CLIENT_NETBIOS_NAME = "PIPESHUB"
REPARSE_POINT = 0x0400
_SMB1_REJECTED = "Server rejected SMB1. Use the SMB connector instead of CIFS."
_T = TypeVar("_T")


def _dialect_mismatch(exc: BaseException) -> bool:
    """SMB1-only negotiate was refused. Bad passwords return False instead of raising."""
    if isinstance(exc, (NotConnectedError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True
    if isinstance(exc, smb_structs.ProtocolError):
        text = str(exc).lower()
        return any(token in text for token in ("dialect", "smb2", "smb 2", "protocol field", "negotiate"))
    return False


def _as_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None


def _smb1_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip("/")
    return f"/{cleaned}" if cleaned else "/"


def _share_kind(share_type: object) -> str:
    if isinstance(share_type, str):
        lowered = share_type.lower()
        if "ipc" in lowered:
            return "ipc"
        if "print" in lowered:
            return "print"
        return "disk"
    try:
        kind = int(share_type) & 0x0F
    except (TypeError, ValueError):
        return "disk"
    return {0: "disk", 1: "print", 2: "device", 3: "ipc"}.get(kind, "disk")


class CifsClient(IClient):
    def __init__(
        self,
        *,
        server: str,
        username: str,
        password: str,
        remote_name: str,
        port: int = 445,
        domain: str = "",
        share: str | None = None,
        use_ntlm_v2: bool = True,
        logger: Logger | None = None,
    ) -> None:
        self.server = server
        self.port = port
        self.username = username
        self.password = password
        self.remote_name = remote_name
        self.domain = domain
        self.default_share = share
        self.use_ntlm_v2 = use_ntlm_v2
        self.is_direct_tcp = port != 139
        self.logger = logger
        self._conn: object | None = None

    def get_client(self) -> object:
        return self._conn

    def connect(self) -> None:
        self._ensure_connected()

    def close(self) -> None:
        if self._conn is not None:
            with suppress(Exception):
                getattr(self._conn, "close")()
            self._conn = None

    def _new_connection(self) -> object:
        return SMBConnection(
            self.username,
            self.password,
            CLIENT_NETBIOS_NAME,
            self.remote_name,
            domain=self.domain,
            use_ntlm_v2=self.use_ntlm_v2,
            sign_options=SMBConnection.SIGN_WHEN_REQUIRED,
            is_direct_tcp=self.is_direct_tcp,
        )

    def _ensure_connected(self) -> None:
        if self._conn is not None:
            return
        conn = self._new_connection()
        try:
            ok = conn.connect(self.server, self.port)
        except Exception as exc:
            with suppress(Exception):
                conn.close()
            if _dialect_mismatch(exc):
                raise DialectError(_SMB1_REJECTED) from exc
            raise NetworkShareAuthError(str(exc)) from exc
        if not ok:
            conn.close()
            raise NetworkShareAuthError("SMB1 authentication failed")
        if getattr(conn, "isUsingSMB2", False):
            conn.close()
            raise DialectError(
                "Server negotiated SMB 2 or later. Use the SMB connector instead of CIFS."
            )
        self._conn = conn

    def _is_disconnect(self, exc: BaseException) -> bool:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        tokens = (
            "timed out",
            "timeout",
            "reset",
            "broken pipe",
            "not connected",
            "notconnected",
            "eof",
            "connection",
        )
        return isinstance(exc, (OSError, ConnectionError, TimeoutError)) or any(
            token in text or token in name for token in tokens
        )

    def _invoke(self, fn: Callable[..., _T], *args: object, **kwargs: object) -> _T:
        self._ensure_connected()
        try:
            return fn(*args, **kwargs)
        except DialectError:
            raise
        except NetworkShareAuthError:
            raise
        except Exception as exc:
            if not self._is_disconnect(exc):
                raise
            self.close()
            self._ensure_connected()
            return fn(*args, **kwargs)

    def list_directory(self, share: str, path: str) -> list[DirectoryEntry]:
        def _list() -> list[DirectoryEntry]:
            listed = self._conn.listPath(share, _smb1_path(path))
            return [self._from_shared_file(item) for item in listed]

        try:
            return self._invoke(_list)
        except FileNotFoundError:
            raise
        except DialectError:
            raise
        except NetworkShareAuthError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "no such" in message:
                raise FileNotFoundError(path) from exc
            if "logon" in message or "access denied" in message:
                raise NetworkShareAuthError(str(exc)) from exc
            raise DirectoryListingError(share, path, str(exc)) from exc

    def stat(self, share: str, path: str) -> DirectoryEntry | None:
        def _stat() -> DirectoryEntry:
            item = self._conn.getAttributes(share, _smb1_path(path))
            return self._from_shared_file(item, name_override=path.replace("\\", "/").rsplit("/", 1)[-1])

        try:
            return self._invoke(_stat)
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "no such" in message:
                return None
            raise DirectoryListingError(share, path, str(exc)) from exc

    def read_chunk(self, share: str, path: str, offset: int, chunk_size: int) -> bytes:
        def _read() -> bytes:
            buf = BytesIO()
            self._conn.retrieveFileFromOffset(
                share, _smb1_path(path), buf, offset=offset, max_length=chunk_size
            )
            return buf.getvalue()

        return self._invoke(_read)

    def list_shares(self) -> list[ShareInfo]:
        def _list() -> list[ShareInfo]:
            devices = self._conn.listShares()
            shares: list[ShareInfo] = []
            for device in devices:
                name = getattr(device, "name", "") or ""
                if not name:
                    continue
                shares.append(ShareInfo(name=name, share_type=_share_kind(getattr(device, "type", 0))))
            return shares

        try:
            return self._invoke(_list)
        except Exception as exc:
            if self.default_share:
                return [ShareInfo(name=self.default_share, share_type="disk")]
            raise ShareListingError(str(exc)) from exc

    def _from_shared_file(self, item: object, name_override: str | None = None) -> DirectoryEntry:
        name = name_override or getattr(item, "filename", "") or ""
        attrs = int(getattr(item, "file_attributes", 0) or 0)
        is_dir_value = getattr(item, "isDirectory", False)
        is_dir = bool(is_dir_value()) if callable(is_dir_value) else bool(is_dir_value)
        file_id = getattr(item, "file_id", None)
        try:
            file_id_int = int(file_id) if file_id is not None else None
        except (TypeError, ValueError):
            file_id_int = None
        return DirectoryEntry(
            name=name,
            is_directory=is_dir,
            is_symlink=False,
            is_reparse=bool(attrs & REPARSE_POINT),
            size=int(getattr(item, "file_size", 0) or 0),
            created_time=_as_datetime(getattr(item, "create_time", None)),
            last_write_time=_as_datetime(getattr(item, "last_write_time", None)),
            file_id=file_id_int,
        )

    @classmethod
    async def build_from_services(
        cls,
        logger: Logger,
        config_service: ConfigurationService,
        connector_instance_id: str,
    ) -> "CifsClient":
        config = await config_service.get_config(
            ConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_instance_id)
        )
        if not config:
            raise NetworkShareAuthError("CIFS configuration not found")
        auth = config.get("auth") or {}
        server = (auth.get("server") or "").strip()
        username = (auth.get("username") or "").strip()
        password = auth.get("password")
        if not server or not username or not password:
            raise NetworkShareAuthError("CIFS server, username, and password are required")
        port = int(auth.get("port") or 445)
        domain = (auth.get("domain") or "").strip()
        share = (auth.get("share") or "").strip() or None
        remote_name = (auth.get("serverName") or "").strip() or server.split(".")[0]
        ntlm = (auth.get("ntlmVersion") or "v2").strip().lower()
        client = cls(
            server=server,
            username=username,
            password=password,
            remote_name=remote_name,
            port=port,
            domain=domain,
            share=share,
            use_ntlm_v2=ntlm != "v1",
            logger=logger,
        )
        await asyncio.to_thread(client.connect)
        return client
