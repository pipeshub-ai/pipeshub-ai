"""Checks on SMB auth settings before they are saved or used."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

DEFAULT_PORT = 445
# SMB over TCP and over NetBIOS. Other ports are for team connectors, which only admins create.
STANDARD_PORTS = frozenset({445, 139})
_MAX_PORT = 65535
# RFC 1123 labels; underscores too, which NetBIOS-era file server names still carry.
_HOSTNAME = re.compile(
    r"(?=.{1,253}\.?$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
    r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*\.?"
)
# Characters Windows does not allow in a share name, path separators included.
_SHARE_FORBIDDEN = frozenset('\\/:*?"<>|')
_MAX_SHARE_LENGTH = 80


def parse_port(value: object) -> int | None:
    """``value`` as a TCP port, or None if it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        port = value
    # int() rejects some characters isdigit() accepts, such as superscripts.
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        port = int(value.strip())
    else:
        return None
    return port if 1 <= port <= _MAX_PORT else None


def is_server_name(server: str) -> bool:
    try:
        ipaddress.ip_address(server.removeprefix("[").removesuffix("]"))
    except ValueError:
        return _HOSTNAME.fullmatch(server) is not None
    return True


def is_share_name(share: str) -> bool:
    return (
        len(share) <= _MAX_SHARE_LENGTH
        and share not in (".", "..")
        and not any(ch in _SHARE_FORBIDDEN or ord(ch) < 32 for ch in share)
    )


def auth_problems(auth: dict[str, Any]) -> list[str]:
    """What is wrong with these settings, each fit to show the user."""
    problems: list[str] = []
    server = str(auth.get("server") or "").strip()
    if not server:
        problems.append("Server is required.")
    elif not is_server_name(server):
        problems.append("Server must be a hostname or an IP address, without \\\\, / or a share name.")
    port = auth.get("port")
    if port not in (None, "") and parse_port(port) is None:
        problems.append("Port must be a whole number from 1 to 65535.")
    if not str(auth.get("username") or "").strip():
        problems.append("Username is required.")
    password = auth.get("password")
    if not password:
        problems.append("Password is required.")
    elif not isinstance(password, str):
        problems.append("Password must be text.")
    share = str(auth.get("share") or "").strip()
    if not share:
        problems.append("Share is required.")
    elif not is_share_name(share):
        problems.append('Share must be a single share name, without \\, /, :, *, ?, ", <, > or |.')
    return problems


def port_of(auth: dict[str, Any]) -> int:
    return parse_port(auth.get("port")) or DEFAULT_PORT
