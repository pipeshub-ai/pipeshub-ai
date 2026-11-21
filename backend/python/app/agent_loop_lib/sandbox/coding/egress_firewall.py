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
import os
import re
import secrets
import shlex
import threading
from collections.abc import Iterable
from typing import Any

__all__ = [
    "BLOCKED_CIDRS",
    "CONTAINER_HARDENING",
    "WRITABLE_DIRS",
    "DEFAULT_OPEN_EGRESS_POOL",
    "ENV_OPEN_EGRESS_SUBNETS",
    "FIREWALL_UNAVAILABLE_EXIT_CODE",
    "build_firewall_rules_script",
    "build_firewall_script",
    "create_sandbox_container",
    "ensure_egress_network_sync",
    "firewall_unavailable",
    "firewalled_container_kwargs",
    "limited",
    "new_firewall_token",
    "open_egress_pool",
    "open_egress_subnets",
    "parse_cidrs",
]

logger = logging.getLogger(__name__)

_ENV_DISK_QUOTA = "SANDBOX_DISK_QUOTA"
_DEFAULT_DISK_QUOTA = "10g"
# Per-process: None = storage_opt not tried yet, True = the daemon accepts it
# (applied), False = it rejects it (runs go uncapped). Set once under the lock.
_storage_opt_usable: bool | None = None
_storage_opt_lock = threading.Lock()


def _disk_quota() -> str | None:
    """Per-run writable-layer cap (``storage_opt size``), or None to disable.

    Blank/0/none/off disables; otherwise a Docker size string such as ``10g``.
    """
    raw = os.environ.get(_ENV_DISK_QUOTA)
    raw = (raw if raw is not None else _DEFAULT_DISK_QUOTA).strip()
    if not raw or raw.lower() in {"0", "none", "off"}:
        return None
    return raw


def create_sandbox_container(client: Any, **kwargs: Any) -> Any:
    """``client.containers.create`` with a per-run writable-layer disk quota.

    Caps a run's writable layer (``/src``, ``/output``, …) via ``storage_opt``
    so one run cannot fill the host disk (CWE-400). The cap is applied wherever
    the daemon accepts the option; whether an accepted cap is ENFORCED depends
    on the storage driver (overlay2 on xfs with ``pquota``, or btrfs/zfs/
    devicemapper, enforce it; overlay2 on ext4 accepts but silently ignores
    it). That ignore case cannot be detected reliably — the daemon returns no
    error, and the docker-socket-proxy blocks the ``/info`` query that would
    reveal the driver — so it is stated in a one-time log rather than probed
    (a write probe is not reliable through the proxy's log streaming). A daemon
    that REJECTS the option falls back to no cap with a warning, so a run is
    never broken. Exceeding an enforced cap fails a write with a clean ENOSPC.
    """
    quota = _disk_quota()
    if quota is None or _storage_opt_usable is False:
        return client.containers.create(**kwargs)
    try:
        container = client.containers.create(storage_opt={"size": quota}, **kwargs)
    except Exception as exc:
        if not _is_storage_opt_rejected(exc):
            raise
        _mark_storage_opt_unusable(quota, exc)
        return client.containers.create(**kwargs)
    _mark_storage_opt_applied(quota)
    return container


def _mark_storage_opt_applied(quota: str) -> None:
    global _storage_opt_usable
    if _storage_opt_usable is None:
        with _storage_opt_lock:
            if _storage_opt_usable is None:
                _storage_opt_usable = True
                logger.info(
                    "Applying a per-run disk quota (%s=%s via storage_opt). Whether it is "
                    "ENFORCED depends on the daemon's storage driver: overlay2 on xfs with "
                    "the pquota mount option, or btrfs/zfs/devicemapper, enforce it; overlay2 "
                    "on ext4 accepts but ignores it. Verify the driver if you rely on the cap.",
                    _ENV_DISK_QUOTA, quota,
                )


def _mark_storage_opt_unusable(quota: str, exc: Exception) -> None:
    global _storage_opt_usable
    if _storage_opt_usable is not False:
        with _storage_opt_lock:
            if _storage_opt_usable is not False:
                _storage_opt_usable = False
                logger.warning(
                    "%s=%s was rejected by this Docker daemon (%s); sandboxes run without a "
                    "per-run disk cap. Use overlay2 on xfs with pquota (or btrfs/zfs) to "
                    "enforce one.",
                    _ENV_DISK_QUOTA, quota, exc,
                )


def _is_storage_opt_rejected(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in (
        "storage-opt", "storage opt", "storageopt", "--storage-opt", "pquota", "size option",
    ))


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

# `user` is the non-root user the stock sandbox image creates (and the same
# name the egress firewall's setpriv drops to); pinning it means an image or
# daemon default that would otherwise run as root is caught. The firewalled
# setup path overrides this back to "0" to install iptables, then setpriv-drops
# to this user before any model code runs.
#
# No RLIMIT_FSIZE here: a per-file cap only bounds one file (a script can still
# fill the disk with many) and exceeding it kills the run with SIGXFSZ rather
# than failing cleanly. The per-run writable-layer quota is applied instead by
# `create_sandbox_container` above (storage_opt, where the driver enforces it),
# which bounds total writes and fails with a clean ENOSPC.
CONTAINER_HARDENING: dict[str, Any] = {
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
    "pids_limit": 256,
    "user": "sandbox",
    # SEC-A-04: the broker's floor (``app.sandbox.sandbox_floor``), which the socket proxy now requires: a read-only
    # root, its writable directories anonymous volumes that go with the container.
    "read_only": True,
}
# The directories a legacy sandbox writes: the image makes them the sandbox user's, and an anonymous volume there
# starts as the image's directory. /home holds the user's own home (npm and pip caches).
WRITABLE_DIRS: tuple[str, ...] = ("/src", "/output", "/install", "/deps", "/node_modules", "/home")


def limited(mem_bytes: int) -> dict[str, Any]:
    """The memory and disk part of the floor for a container of ``mem_bytes``: no swap beyond it, and the writable
    directories as anonymous volumes."""
    from docker.types import Mount

    return {
        "mem_limit": mem_bytes,
        "memswap_limit": mem_bytes,
        "mounts": [Mount(target, None, type="volume") for target in WRITABLE_DIRS],
    }

# NET_ADMIN installs the rules; SETUID/SETGID/SETPCAP let setpriv switch user
# and empty the bounding set. All are gone before the command runs.
_FIREWALL_SETUP_CAPS = ["NET_ADMIN", "SETUID", "SETGID", "SETPCAP"]
_ICC_OPTION = "com.docker.network.bridge.enable_icc"

# The shared open-egress bridge takes its subnet from this pool, never from the daemon's default address pools: a
# daemon has about 30 of those, and on a host that runs other stacks they are all taken, so a bridge created without
# an address would fail every open-egress sandbox. Next to the broker's per-sandbox pool (10.213/16).
ENV_OPEN_EGRESS_SUBNETS = "SANDBOX_OPEN_EGRESS_SUBNETS"
DEFAULT_OPEN_EGRESS_POOL = "10.212.0.0/16"
# Every open-egress sandbox of the install joins the one bridge, so it holds 1021 of them.
_OPEN_EGRESS_PREFIX = 22


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
    user = shlex.quote(run_as)
    lines = _rule_lines(network_cidrs, allowed_cidrs, token)
    lines.insert(2, "command -v setpriv >/dev/null 2>&1 || fail 'setpriv not found'")
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


def build_firewall_rules_script(
    network_cidrs: Iterable[str],
    allowed_cidrs: Iterable[str] = (),
    *,
    token: str,
) -> str:
    """``sh -c`` script that only installs the rules and exits.

    For a one-shot helper that shares a sandbox's network namespace (the
    sandbox broker's containers have no capabilities to do it themselves):
    the rules live in the namespace, so they hold for the sandbox after the
    helper is gone.
    """
    return "\n".join([*_rule_lines(network_cidrs, allowed_cidrs, token), "exit 0"]) + "\n"


def _rule_lines(network_cidrs: Iterable[str], allowed_cidrs: Iterable[str], token: str) -> list[str]:
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("firewall token must be 16-64 lowercase hex characters")
    blocked = list(dict.fromkeys([*BLOCKED_CIDRS, *_ipv4_cidrs(network_cidrs)]))
    allowed = _ipv4_cidrs(allowed_cidrs)
    lines = [
        "set -u",
        f"fail() {{ echo '{_UNAVAILABLE_MARKER} ({token}):' \"$1\" >&2; exit {FIREWALL_UNAVAILABLE_EXIT_CODE}; }}",
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
    return lines


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


def open_egress_pool(raw: str | None = None) -> ipaddress.IPv4Network:
    """The pool the open-egress bridge's subnet comes from: ``raw``, else ``SANDBOX_OPEN_EGRESS_SUBNETS``, else the
    default. A pool that is not one IPv4 network is refused rather than replaced: a typo must not move the bridge
    onto addresses this host routes to."""
    value = (raw if raw is not None else os.environ.get(ENV_OPEN_EGRESS_SUBNETS) or "").strip()
    network = ipaddress.ip_network(value or DEFAULT_OPEN_EGRESS_POOL, strict=True)
    if not isinstance(network, ipaddress.IPv4Network) or network.prefixlen > 29:
        raise ValueError(f"{ENV_OPEN_EGRESS_SUBNETS}={value!r} must be an IPv4 network of 8+ addresses")
    return network


def open_egress_subnets(pool: ipaddress.IPv4Network) -> list[str]:
    """The subnets the bridge may take, lowest first: the pool's /22s, or the pool itself when it is smaller."""
    prefix = max(_OPEN_EGRESS_PREFIX, pool.prefixlen)
    return [str(net) for net in pool.subnets(new_prefix=prefix)]


def _create_egress_network(
    client: Any, network_name: str, labels: dict[str, str], candidates: Iterable[str],
) -> None:
    from docker.types import IPAMConfig, IPAMPool

    from app.harness.sandbox.docker_egress import is_pool_overlap

    tried = 0
    for subnet in dict.fromkeys(candidates):
        tried += 1
        gateway = str(next(ipaddress.ip_network(subnet).hosts()))
        try:
            client.networks.create(
                name=network_name,
                driver="bridge",
                internal=False,
                enable_ipv6=False,
                options={_ICC_OPTION: "false"},
                ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=subnet, gateway=gateway)]),
                labels=labels,
                check_duplicate=True,
            )
        except Exception as exc:
            if is_pool_overlap(exc):
                continue
            raise
        logger.info("created sandbox egress network %s on %s", network_name, subnet)
        return
    raise RuntimeError(
        f"no free subnet for sandbox egress network {network_name} after {tried} tries; "
        f"set {ENV_OPEN_EGRESS_SUBNETS} to a free IPv4 range"
    )


def ensure_egress_network_sync(
    client: Any, network_name: str, labels: dict[str, str], *, pool: str | None = None,
) -> list[str]:
    """Create the egress bridge with inter-container traffic off and return
    its IPv4 subnets (empty if they can't be read).

    A network created before ICC was turned off is replaced while idle, on
    its old subnet when that is still free; one with containers attached is
    kept (removing it would fail) — the per-container firewall blocks the
    bridge subnet either way. A new bridge takes a subnet from ``pool``
    (``open_egress_pool``).
    """
    network = _get_network(client, network_name)
    previous: list[str] = []
    if network is not None and (network.attrs.get("Options") or {}).get(_ICC_OPTION) != "false":
        if network.attrs.get("Containers"):
            logger.warning(
                "sandbox egress network %s allows inter-container traffic and is in use; "
                "it will be recreated once idle", network_name,
            )
        else:
            logger.info("recreating sandbox egress network %s with inter-container traffic off", network_name)
            previous = _subnets(network.attrs)
            network.remove()
            network = None
    if network is None:
        try:
            _create_egress_network(
                client, network_name, labels, [*previous, *open_egress_subnets(open_egress_pool(pool))],
            )
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
