"""A repository an agent names: an ``https://`` URL to one repository on an allowed git host, and nothing else.

The URL is the model's text, so it is read strictly: https on port 443 only (``http``, ``ssh``, ``git@host:``,
``file:`` and ``ext::`` never), no user, password, query or fragment, a host name (never an address), and a path of
plain segments naming one repository. What an admin allows is a list of hosts, each optionally limited to owner or
``owner/repo`` prefixes (``allowed_entry``).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from harness_core.sandbox.egress.git import branch_ok, repository_ok
from harness_core.sandbox.egress.policy import is_host_name, normalize_host

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

HTTPS_PORT: Final = 443
MAX_URL_CHARS: Final = 400
_PREFIX: Final = re.compile(r"[A-Za-z0-9_.-]+(?:/(?:[A-Za-z0-9_.-]+)?)?")


class RepositoryRefused(Exception):
    """A fetch that will not happen, told in plain words the model and the person read; ``reason`` is the code the
    activity and the logs keep."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class RepositoryLocation:
    """One repository: its host and its path (``owner/name``, or a deeper group path), without ``.git``."""

    host: str
    path: str

    @property
    def url(self) -> str:
        """What the fetch asks for: the path with ``.git``, which every git host serves."""
        return f"https://{self.host}/{self.path}.git"

    @property
    def label(self) -> str:
        """How a person reads it: ``github.com/onyx-dot-app/onyx``."""
        return f"{self.host}/{self.path}"

    @property
    def shown_url(self) -> str:
        return f"https://{self.host}/{self.path}"


def _address(host: str) -> bool:
    try:
        _ = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host.split(".")[-1].isdigit()
    return True


def parse_repository_url(raw: object) -> RepositoryLocation:
    """The repository ``raw`` names, or ``RepositoryRefused`` saying what is wrong with it."""
    if not isinstance(raw, str) or not raw.strip():
        raise RepositoryRefused("repository_url_invalid", "Give the repository's https URL, like https://github.com/owner/repo.")
    text = raw.strip()
    if len(text) > MAX_URL_CHARS or any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in text):
        raise RepositoryRefused("repository_url_invalid", "The repository URL is too long or holds characters a URL cannot.")
    if re.match(r"^[^/:@]+@[^:]+:", text):
        raise RepositoryRefused("repository_url_invalid", "Only https URLs can be cloned; use the repository's https address.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise RepositoryRefused("repository_url_invalid", "The repository URL cannot be read.") from None
    if parts.scheme.lower() != "https":
        raise RepositoryRefused("repository_url_invalid", "Only https URLs can be cloned; http, ssh and file URLs never.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise RepositoryRefused("repository_url_invalid", "A repository URL carries no user name or password.")
    if parts.query or parts.fragment or "%" in parts.path or "\\" in parts.path:
        raise RepositoryRefused("repository_url_invalid", "A repository URL has no query, fragment or escaped characters.")
    if port not in (None, HTTPS_PORT):
        raise RepositoryRefused("repository_url_invalid", "Repositories are cloned over https on port 443 only.")
    host = normalize_host(parts.hostname or "")
    if not host or not is_host_name(host) or _address(host) or "." not in host:
        raise RepositoryRefused("repository_url_invalid", "Name the git host by its name, like github.com, not an address.")
    path = parts.path.strip("/").removesuffix(".git")
    if len(path.split("/")) < 2 or not repository_ok(path):
        raise RepositoryRefused("repository_url_invalid", "The URL must name one repository, like https://github.com/owner/repo.")
    return RepositoryLocation(host, path)


def check_ref(ref: object) -> str | None:
    """A branch or tag name to clone, or ``None`` for the repository's default branch."""
    if ref is None or ref == "":
        return None
    if not isinstance(ref, str) or not branch_ok(ref):
        raise RepositoryRefused("repository_ref_invalid", "The ref is a branch or tag name, like main or v1.2.")
    return ref


def prefix_covers(prefix: str, path: str) -> bool:
    """``onyx-dot-app/`` or ``onyx-dot-app`` covers every repository of that owner; ``onyx-dot-app/onyx`` covers that
    repository (and none whose name only starts the same). Case-insensitive, as the hosts treat owners."""
    wanted = path.lower()
    limit = prefix.lower()
    if limit.endswith("/") or "/" not in limit:
        return wanted.startswith(limit.rstrip("/") + "/")
    return wanted == limit or wanted.startswith(limit + "/")


def allowed_entry(location: RepositoryLocation, hosts: Sequence[Mapping[str, JsonValue]]) -> Mapping[str, JsonValue] | None:
    """The admin's entry that allows ``location``: its host, and one of its prefixes when it lists any."""
    for entry in hosts:
        if str(entry.get("host", "")).lower() != location.host:
            continue
        prefixes = entry.get("prefixes")
        if prefixes is None:
            return entry
        if isinstance(prefixes, list) and any(
            isinstance(p, str) and _PREFIX.fullmatch(p) and prefix_covers(p, location.path) for p in prefixes
        ):
            return entry
    return None


__all__ = [
    "HTTPS_PORT",
    "RepositoryLocation",
    "RepositoryRefused",
    "allowed_entry",
    "check_ref",
    "parse_repository_url",
    "prefix_covers",
]
