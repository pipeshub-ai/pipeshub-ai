"""Release data for a person's own sign-in (SIGNIN, plan §2.5): the hosts per sandbox-login vendor row.

``vendor_hosts.json`` names, for each row whose engine signs in inside its sandbox, where that sandbox may tunnel
and which hosts a printed sign-in link may point at. It is signed with the vendor rules' release key
(``vendor_hosts.json.sig``, the same ``RELEASE_KEYS``): a deployment cannot widen where a signed-in sandbox reaches.
Without a valid signature there are no hosts, and no own sign-in runs (``signin_unavailable``).
"""

from __future__ import annotations

import ipaddress
import json
import re
from functools import cache
from typing import TYPE_CHECKING, Final, cast

from harness_core.access.errors import VendorRulesInvalid
from harness_core.access.vendor_rules import verify_release_document
from harness_core.engines.signin import SignInHosts
from harness_engines.vendor_rules import packaged

if TYPE_CHECKING:
    from collections.abc import Mapping

HOSTS_RESOURCE: Final = "vendor_hosts.json"
HOSTS_SIGNATURE_RESOURCE: Final = "vendor_hosts.json.sig"
DOCUMENT_SCHEMA: Final = "pipeshub.vendor-hosts/1"
CLAUDE_PLAN_ROW: Final = "anthropic-claude-plan-claude-code"
# A DNS name only: no IP literal, wildcard, port or path can be listed.
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _invalid(message: str) -> VendorRulesInvalid:
    return VendorRulesInvalid(message, detail="malformed")


def _hosts(raw: object, what: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise _invalid(f"{what} is not a list of hosts")
    hosts = cast("list[object]", raw)
    for host in hosts:
        if not isinstance(host, str) or not _HOST.match(host) or _is_ip(host):
            raise _invalid(f"{what} lists {host!r}, which is not a host name")
    return tuple(cast("list[str]", hosts))


def _is_ip(value: str) -> bool:
    try:
        _ = ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def parse_vendor_hosts(document: bytes) -> Mapping[str, SignInHosts]:
    try:
        raw: object = json.loads(document)
    except ValueError:
        raise _invalid("the vendor hosts are not JSON") from None
    if not isinstance(raw, dict):
        raise _invalid("the vendor hosts are not an object")
    doc = cast("dict[str, object]", raw)
    rows = doc.get("rows")
    if doc.get("schema") != DOCUMENT_SCHEMA or not isinstance(rows, dict):
        raise _invalid("the vendor hosts are not this release's schema")
    out: dict[str, SignInHosts] = {}
    for row_id, entry in cast("dict[str, object]", rows).items():
        if not isinstance(entry, dict) or set(cast("dict[str, object]", entry)) != {"sandbox", "link"}:
            raise _invalid(f"row {row_id} is not a sandbox and link host list")
        fields = cast("dict[str, object]", entry)
        out[row_id] = SignInHosts(
            sandbox=_hosts(fields["sandbox"], f"{row_id}.sandbox"),
            link=frozenset(_hosts(fields["link"], f"{row_id}.link")),
        )
    return out


def load_vendor_hosts(
    document: bytes | None, signature: bytes | None, keys: Mapping[str, bytes] | None = None
) -> Mapping[str, SignInHosts]:
    """Verified against ``keys``, by default the release keys in force; raises ``VendorRulesInvalid``."""
    if keys is None:
        from harness_engines.release_keys import trusted_release_keys

        keys = trusted_release_keys()
    return parse_vendor_hosts(verify_release_document(document, signature, keys))


@cache
def _release(trusted: frozenset[str]) -> Mapping[str, SignInHosts] | None:
    del trusted
    try:
        return load_vendor_hosts(packaged(HOSTS_RESOURCE), packaged(HOSTS_SIGNATURE_RESOURCE))
    except VendorRulesInvalid:
        return None


def release_sign_in_hosts(row_id: str = CLAUDE_PLAN_ROW) -> SignInHosts | None:
    """The packaged, signed hosts of a row; ``None`` while they are unsigned or their key is no longer in force (own
    sign-in stays off)."""
    from harness_engines.release_keys import trusted_release_keys

    hosts = _release(frozenset(trusted_release_keys()))
    return hosts.get(row_id) if hosts is not None else None


__all__ = [
    "CLAUDE_PLAN_ROW",
    "DOCUMENT_SCHEMA",
    "HOSTS_RESOURCE",
    "HOSTS_SIGNATURE_RESOURCE",
    "load_vendor_hosts",
    "parse_vendor_hosts",
    "release_sign_in_hosts",
]
