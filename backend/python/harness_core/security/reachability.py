"""Whether content fetched from an address is public (WP-83 D1, WP83-FU-3).

Public means anyone on the internet could have read it. The dialled address being public is necessary but not enough:
a host on a public address that answers only the organization's own callers (an IP-allowlisted intranet, a
self-hosted git server) serves the organization's private material. PipesHub never probes from outside to find out
(that would send every URL a run reads, tokens included, to a third party); the admin declares instead:

- ``websites.orgOnlyAddresses`` at the org layer: host names, ``*.`` domains, addresses and CIDR ranges only the
  organization can reach. Content from them is never public.
- ``repositories.hosts[].publicReadable``: a git host an admin added by hand is not public-readable until the admin
  says so; a preset's host (``github.com public repos``) is.

``fetch_url`` and ``repository_fetch`` both ask this one classifier. Anything it cannot clear stays non-public (fail
closed): an unknown address while the org declares ranges, an org-only name, a non-public address.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from harness_core.sandbox.egress.policy import is_public_address

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.sandbox.egress.policy import Network

ORG_ONLY_ADDRESSES: Final = "orgOnlyAddresses"
PUBLIC_READABLE: Final = "publicReadable"
MAX_ORG_ONLY: Final = 100
# ``OrgOnlyAddress``'s name branch: lower-case labels, the last starting with a letter (so never a bad address).
_NAME: Final = re.compile(r"(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _network(entry: str) -> Network | None:
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None


def _name(entry: str) -> str | None:
    """A host name or ``*.`` domain in the form the contract's ``OrgOnlyAddress`` allows; ``None`` otherwise."""
    return entry if _NAME.fullmatch(entry) else None


@dataclass(frozen=True, slots=True)
class PublicReachability:
    """One org's answer to "could anyone have read what came from here". ``networks`` and ``names``: its org-only
    addresses; ``refused``: the deployment's own ranges, never public either."""

    networks: tuple[Network, ...] = ()
    names: frozenset[str] = frozenset()
    refused: tuple[Network, ...] = ()
    # False: the org's addresses could not be read, so nothing clears.
    read: bool = True

    @classmethod
    def of(cls, entries: Iterable[object], refused: Sequence[Network] = ()) -> PublicReachability:
        """From an org's ``orgOnlyAddresses`` as stored. An entry this worker cannot read is skipped: Node refuses
        such a save, so only a newer form can reach here."""
        networks: list[Network] = []
        names: set[str] = set()
        for raw in list(entries)[:MAX_ORG_ONLY]:
            if not isinstance(raw, str) or not raw.strip():
                continue
            network = _network(raw) if "/" in raw or ":" in raw or raw[-1:].isdigit() else None
            if network is not None:
                networks.append(network)
            elif (name := _name(raw)) is not None:
                names.add(name)
        return cls(tuple(networks), frozenset(names), tuple(refused))

    @property
    def declares_ranges(self) -> bool:
        """Whether an address must be known to clear content (the org lists addresses or ranges)."""
        return bool(self.networks)

    def name_org_only(self, host: str) -> bool:
        name = host.strip().lower().rstrip(".")
        if name in self.names:
            return True
        return any(entry.startswith("*.") and name.endswith(entry[1:]) for entry in self.names)

    def address_public(self, address: str) -> bool:
        """The dialled address is public and outside the deployment's and the org's declared ranges."""
        return is_public_address(address, (*self.refused, *self.networks))

    def org_only(self, host: str, addresses: Sequence[str]) -> bool:
        """Whether content from ``host`` reached at ``addresses`` may be the organization's alone: an org-only name,
        an address in a declared range, or, while the org declares ranges, no address to check against them."""
        if not self.read or self.name_org_only(host):
            return True
        if not self.declares_ranges:
            return False
        return not addresses or any(self._in_ranges(a) is not False for a in addresses)

    def _in_ranges(self, address: str) -> bool | None:
        """Whether ``address`` is in one of the org's declared ranges; ``None`` for what is not an address."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return any(ip.version == net.version and ip in net for net in self.networks)

    def repository_public(self, host: str, address: str | None, entry: Mapping[str, JsonValue] | None) -> bool:
        """Whether a clone the fetcher made from a public address (``FetchedRepository.public``) is public too by the
        org's word: its host is one anyone can read (``entry``, the allowed host it was allowed by) and not org-only."""
        return publicly_readable(entry) and not self.org_only(host, () if address is None else (address,))


UNREADABLE: Final = PublicReachability(read=False)


def publicly_readable(entry: Mapping[str, JsonValue] | None) -> bool:
    """An allowed git host anyone on the internet can read: a preset's, or one the admin marked so."""
    if entry is None:
        return False
    return bool(entry.get("preset")) or entry.get(PUBLIC_READABLE) is True


def org_only_entries(websites: Mapping[str, JsonValue] | None) -> list[object]:
    """The org layer's ``orgOnlyAddresses`` from the live layer's ``websites`` member (org layer only)."""
    org = websites.get("org") if websites is not None else None
    listed = cast("Mapping[str, JsonValue]", org).get(ORG_ONLY_ADDRESSES) if isinstance(org, Mapping) else None
    return list(listed) if isinstance(listed, list) else []


__all__ = [
    "MAX_ORG_ONLY",
    "ORG_ONLY_ADDRESSES",
    "PUBLIC_READABLE",
    "UNREADABLE",
    "PublicReachability",
    "org_only_entries",
    "publicly_readable",
]
