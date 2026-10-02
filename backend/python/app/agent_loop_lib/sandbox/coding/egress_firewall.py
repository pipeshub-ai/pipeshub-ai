"""Egress policy for sandbox containers that join the egress bridge.

A Docker bridge only keeps sandboxed code off sibling containers *by name*.
By address it still reaches the bridge gateway (the host, or under
Docker-in-Docker the application pod itself), every RFC1918 / CGNAT range the
host can route to, and the cloud metadata endpoints.

So every container that joins the bridge starts as root with just enough
capability to install ``OUTPUT`` rules in its own network namespace, then
``exec``s the real command through ``setpriv`` as an unprivileged user with
an empty capability bounding set and ``no_new_privs``. The rules are in place
before any untrusted code runs, live and die with the container, and the
code can neither change them (no ``NET_ADMIN``) nor go around them (no
``NET_RAW``). Nothing on the host is modified.

If the image can't install the rules (no ``iptables``/``setpriv``), the
container exits with ``FIREWALL_UNAVAILABLE_EXIT_CODE`` and a marker on
stderr before running the command; callers detect that with
``firewall_unavailable``. The marker carries a per-container token that only
the setup script holds — ``exec setpriv`` replaces the script before the
command starts — so the command can't fake the signal.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import secrets
import shlex
from collections.abc import Iterable
from typing import Any

__all__ = [
    "BLOCKED_CIDRS",
    "CONTAINER_HARDENING",
    "FIREWALL_UNAVAILABLE_EXIT_CODE",
    "build_firewall_script",
    "ensure_egress_network_sync",
    "firewall_unavailable",
    "firewalled_container_kwargs",
    "new_firewall_token",
    "parse_cidrs",
]

logger = logging.getLogger(__name__)

# Rejected before any operator allowance: link-local carries the AWS/GCP/
# Azure/OpenStack metadata endpoint, and Azure's wireserver is a public IP.
_NEVER_ALLOWED_CIDRS = ("169.254.0.0/16", "168.63.129.16/32")
BLOCKED_CIDRS = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "224.0.0.0/4",
    "240.0.0.0/4",
)

FIREWALL_UNAVAILABLE_EXIT_CODE = 222
_UNAVAILABLE_MARKER = "[sandbox-egress] firewall unavailable"

CONTAINER_HARDENING: dict[str, Any] = {
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
    "pids_limit": 256,
}

# NET_ADMIN installs the rules; SETUID/SETGID/SETPCAP let setpriv switch user
# and empty the bounding set. All are gone before the command runs.
_FIREWALL_SETUP_CAPS = ["NET_ADMIN", "SETUID", "SETGID", "SETPCAP"]
_ICC_OPTION = "com.docker.network.bridge.enable_icc"


_TOKEN_RE = re.compile(r"[0-9a-f]{16,64}")


def new_firewall_token() -> str:
    return secrets.token_hex(16)


def _ipv4_cidrs(cidrs: Iterable[str]) -> list[str]:
    """Normalised IPv4 networks. Anything else raises: each one is spliced
    into a script that runs as root before the privilege drop."""
    normalised = []
    for cidr in cidrs:
        network = ipaddress.ip_network(cidr, strict=False)
        if network.version != 4:
            raise ValueError(f"not an IPv4 network: {cidr!r}")
        normalised.append(str(network))
    return normalised


def build_firewall_script(
    network_cidrs: Iterable[str],
    allowed_cidrs: Iterable[str] = (),
    run_as: str = "sandbox",
    *,
    token: str,
) -> str:
    """``sh -c`` script: install the rules, then exec ``"$@"`` as ``run_as``.

    Blocking the bridge's own subnets covers the gateway and sibling
    sandboxes even when the daemon's address pools sit outside the private
    ranges. Loopback stays open because Docker's embedded DNS (127.0.0.11)
    is reached through it.
    """
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("firewall token must be 16-64 lowercase hex characters")
    blocked = list(dict.fromkeys([*BLOCKED_CIDRS, *_ipv4_cidrs(network_cidrs)]))
    allowed = _ipv4_cidrs(allowed_cidrs)
    user = shlex.quote(run_as)
    lines = [
        "set -u",
        f"fail() {{ echo '{_UNAVAILABLE_MARKER} ({token}):' \"$1\" >&2; exit {FIREWALL_UNAVAILABLE_EXIT_CODE}; }}",
        "command -v setpriv >/dev/null 2>&1 || fail 'setpriv not found'",
        'IPT=""',
        "for b in iptables iptables-legacy; do",
        '  if command -v "$b" >/dev/null 2>&1 && "$b" -w -n -L OUTPUT >/dev/null 2>&1; then IPT="$b"; break; fi',
        "done",
        "[ -n \"$IPT\" ] || fail 'no working iptables in the sandbox image'",
        "r() { \"$IPT\" -w \"$@\" || fail \"iptables $*\"; }",
        "r -A OUTPUT -o lo -j ACCEPT",
    ]
    lines += [f"r -A OUTPUT -d {cidr} -j REJECT" for cidr in _NEVER_ALLOWED_CIDRS]
    lines += [f"r -A OUTPUT -d {cidr} -j ACCEPT" for cidr in allowed]
    lines += [f"r -A OUTPUT -d {cidr} -j REJECT" for cidr in blocked]
    # The container starts as root, so HOME is /root; npm/npx would then
    # fail to write their cache once running as `run_as`.
    lines += [
        f'h="$(getent passwd {user} | cut -d: -f6)" || true',
        '[ -n "$h" ] && export HOME="$h"',
        f"export USER={user} LOGNAME={user}",
    ]
    lines.append(
        f"exec setpriv --reuid={user} --regid={user} --init-groups "
        '--inh-caps=-all --bounding-set=-all --no-new-privs -- "$@"'
    )
    return "\n".join(lines) + "\n"


def firewalled_container_kwargs(
    command: list[str],
    *,
    network_cidrs: Iterable[str],
    allowed_cidrs: Iterable[str] = (),
    run_as: str = "sandbox",
    token: str,
) -> dict[str, Any]:
    """``containers.create`` kwargs that run ``command`` behind the egress
    firewall. Replaces ``command`` and ``user``; hardening is included.
    Pass the same ``token`` to ``firewall_unavailable`` for the result."""
    script = build_firewall_script(network_cidrs, allowed_cidrs, run_as, token=token)
    return {
        **CONTAINER_HARDENING,
        "command": ["sh", "-c", script, "sandbox-egress", *command],
        "user": "0",
        "cap_add": list(_FIREWALL_SETUP_CAPS),
    }


def firewall_unavailable(exit_code: int, stderr: str, token: str) -> bool:
    """True when a firewalled container stopped before running its command.
    Without the container's token a program could claim this to get re-run."""
    return (
        bool(token)
        and exit_code == FIREWALL_UNAVAILABLE_EXIT_CODE
        and f"{_UNAVAILABLE_MARKER} ({token}):" in stderr
    )


def _subnets(attrs: dict[str, Any]) -> list[str]:
    configs = (attrs.get("IPAM") or {}).get("Config") or []
    return list(parse_cidrs(c["Subnet"] for c in configs if c.get("Subnet") and ":" not in c["Subnet"]))


def _get_network(client: Any, network_name: str) -> Any | None:
    # The `names` filter matches substrings, so `sandbox_egress` would
    # otherwise find `pipeshub_sandbox_egress`.
    for network in client.networks.list(names=[network_name]):
        if getattr(network, "name", None) == network_name:
            return client.networks.get(network.id)
    return None


def ensure_egress_network_sync(client: Any, network_name: str, labels: dict[str, str]) -> list[str]:
    """Create the egress bridge with inter-container traffic off and return
    its IPv4 subnets (empty if they can't be read).

    A network created before ICC was turned off is replaced while idle; one
    with containers attached is kept (removing it would fail) — the
    per-container firewall blocks the bridge subnet either way.
    """
    network = _get_network(client, network_name)
    if network is not None and (network.attrs.get("Options") or {}).get(_ICC_OPTION) != "false":
        if network.attrs.get("Containers"):
            logger.warning(
                "sandbox egress network %s allows inter-container traffic and is in use; "
                "it will be recreated once idle", network_name,
            )
        else:
            logger.info("recreating sandbox egress network %s with inter-container traffic off", network_name)
            network.remove()
            network = None
    if network is None:
        try:
            client.networks.create(
                name=network_name,
                driver="bridge",
                internal=False,
                enable_ipv6=False,
                options={_ICC_OPTION: "false"},
                labels=labels,
                check_duplicate=True,
            )
            logger.info("created sandbox egress network %s", network_name)
        except Exception as exc:
            # Another process may have created it between our look and our create.
            logger.debug("egress network creation raised %s; re-checking", exc)
            try:
                raced = _get_network(client, network_name)
            except Exception:
                raced = None
            if raced is None:
                raise
        network = _get_network(client, network_name)
    return _subnets(network.attrs) if network is not None else []


def parse_cidrs(raw: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalise operator-supplied IPv4 CIDRs, dropping (and logging) invalid
    entries — a typo narrows what is allowed rather than widening it."""
    if not raw:
        return ()
    items = raw.split(",") if isinstance(raw, str) else raw
    cidrs: list[str] = []
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            logger.warning("ignoring invalid sandbox egress CIDR %r", item)
            continue
        if network.version != 4:
            logger.warning("ignoring non-IPv4 sandbox egress CIDR %r (the bridge has no IPv6)", item)
            continue
        cidrs.append(str(network))
    return tuple(dict.fromkeys(cidrs))
