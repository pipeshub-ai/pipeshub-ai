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

import functools
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
    "CLOUD_DNS_SERVERS",
    "CONTAINER_HARDENING",
    "FIREWALL_UNAVAILABLE_EXIT_CODE",
    "build_firewall_script",
    "create_sandbox_container",
    "deadline_command",
    "dns_servers_from_env",
    "EgressNetworkUnsafe",
    "ensure_egress_network_sync",
    "firewall_unavailable",
    "firewalled_container_kwargs",
    "new_firewall_token",
    "parse_cidrs",
    "sandbox_labels",
]

logger = logging.getLogger(__name__)

_ENV_DISK_QUOTA = "SANDBOX_DISK_QUOTA"
_DEFAULT_DISK_QUOTA = "10g"
# Per-process: None = storage_opt not tried yet, True = the daemon accepts it
# (applied), False = it rejects it (runs go uncapped). Set once under the lock.
_storage_opt_usable: bool | None = None
_storage_opt_lock = threading.Lock()
# Sandboxes are the first thing the kernel kills when memory runs out, ahead
# of the databases and the app. False once an older Docker proxy refused it.
_OOM_SCORE_ADJ = 1000
_oom_score_adj_usable = True


def _disk_quota() -> str | None:
    """Per-run writable-layer cap (``storage_opt size``), or None to disable.

    Blank/0/none/off disables; otherwise a Docker size string such as ``10g``.
    """
    raw = os.environ.get(_ENV_DISK_QUOTA)
    raw = (raw if raw is not None else _DEFAULT_DISK_QUOTA).strip()
    if not raw or raw.lower() in {"0", "none", "off"}:
        return None
    return raw


# Labels on every sandbox container, so an operator (or a sweep) can find
# the ones a stack left behind, with or without the Docker proxy.
SANDBOX_LABEL = "ai.pipeshub.sandbox"
SANDBOX_STATE_LABEL = "ai.pipeshub.sandbox.state-id"
_STATE_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,63}")


def sandbox_labels(purpose: str = "run") -> dict[str, str]:
    """The app's labels for a sandbox container: what it is for (``run`` or
    ``install``) and this stack's ``SANDBOX_STATE_ID`` when one is set."""
    labels = {SANDBOX_LABEL: purpose}
    state_id = (os.environ.get("SANDBOX_STATE_ID") or "").strip()
    if _STATE_ID_RE.fullmatch(state_id):
        labels[SANDBOX_STATE_LABEL] = state_id
    return labels


def create_sandbox_container(client: Any, *, purpose: str = "run", **kwargs: Any) -> Any:
    """``client.containers.create`` with a per-run writable-layer disk quota,
    marked first to kill when memory runs out (``oom_score_adj=1000``).

    Swap is off (``memswap_limit`` equal to ``mem_limit``), as the Docker
    proxy enforces, so the memory cap means the same with a socket mounted
    straight from the daemon. The container carries ``sandbox_labels(purpose)``.

    A Docker proxy older than the field refuses every create that sets it, so
    on that refusal the container is created without it, and later creates
    skip it, with one warning.

    Caps a run's writable layer (``/src``, ``/output``, …) via ``storage_opt``
    so one run cannot fill the host disk (CWE-400). The cap is applied wherever
    the daemon accepts the option; whether an accepted cap is ENFORCED depends
    on the storage driver (overlay2 on xfs with ``pquota``, or btrfs/zfs/
    devicemapper, enforce it; overlay2 on ext4 accepts but silently ignores
    it). That ignore case is not detected — the daemon returns no error, and
    ``/info`` (also through the docker-socket-proxy) names the storage driver
    and its backing filesystem but not its mount options — so it is stated in
    a one-time log rather than probed (a write probe is not reliable through
    the proxy's log streaming). A daemon
    that REJECTS the option falls back to no cap with a warning, so a run is
    never broken. Exceeding an enforced cap fails a write with a clean ENOSPC.
    """
    global _oom_score_adj_usable
    kwargs["labels"] = {**sandbox_labels(purpose), **(kwargs.get("labels") or {})}
    if kwargs.get("mem_limit") is not None:
        kwargs.setdefault("memswap_limit", kwargs["mem_limit"])
    if _oom_score_adj_usable:
        try:
            return _create_with_quota(client, oom_score_adj=_OOM_SCORE_ADJ, **kwargs)
        except Exception as exc:
            if not _is_oom_score_adj_refused(exc):
                raise
            if _oom_score_adj_usable:
                _oom_score_adj_usable = False
                logger.warning(
                    "sandbox containers are not marked first-to-kill: the Docker proxy "
                    "predates OomScoreAdj; upgrade it (%s)", exc,
                )
    return _create_with_quota(client, **kwargs)


def _is_oom_score_adj_refused(exc: Exception) -> bool:
    """The closed-field refusal of a proxy that does not know the field. Not
    its range check, which is a real misconfiguration."""
    msg = str(exc)
    return "'OomScoreAdj'" in msg and "is not permitted" in msg


def _create_with_quota(client: Any, **kwargs: Any) -> Any:
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

# Resolvers the embedded DNS server (127.0.0.11) may forward to from the
# sandbox's own network namespace, where these rules apply: Docker does so for
# servers set with the daemon's ``dns`` option or ``--dns``. Only port 53 is
# opened on them, ahead of the rejects; their other ports stay closed. Azure
# DNS shares the wireserver address, AWS's resolver and GCP's metadata server
# are link-local. The container's default gateway, and the servers Docker
# lists in its resolv.conf as forwarded from the container, are added when the
# rules are installed; ``SANDBOX_EGRESS_DNS_SERVERS`` (comma-separated IPv4
# addresses) adds more. Code that can query the embedded server can already
# reach these resolvers through it, so this opens no new service.
_ENV_DNS_SERVERS = "SANDBOX_EGRESS_DNS_SERVERS"
CLOUD_DNS_SERVERS = ("168.63.129.16", "169.254.169.253", "169.254.169.254")
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
}

# NET_ADMIN installs the rules; SETUID/SETGID/SETPCAP let setpriv switch user
# and empty the bounding set. All are gone before the command runs.
_FIREWALL_SETUP_CAPS = ["NET_ADMIN", "SETUID", "SETGID", "SETPCAP"]
_ICC_OPTION = "com.docker.network.bridge.enable_icc"


_TOKEN_RE = re.compile(r"[0-9a-f]{16,64}")

# Helpers for /proc/net/route (little-endian hex). Fields: Iface Destination
# Gateway Flags RefCnt Use Metric Mask.
_ROUTE_HELPER_LINES = [
    "ip4() { h=$1; printf '%d.%d.%d.%d' \"0x${h#??????}\" \"0x$(echo \"$h\" | cut -c5-6)\" "
    "\"0x$(echo \"$h\" | cut -c3-4)\" \"0x$(echo \"$h\" | cut -c1-2)\"; }",
    "hex8() { case $1 in [0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]) "
    "return 0;; esac; return 1; }",
    "[ -r /proc/net/route ] || fail 'cannot read /proc/net/route'",
]

# Opens port 53 (udp and tcp) on the default gateway and on every IPv4 server
# Docker's resolv.conf lists as forwarded from the container (``host(...)``
# entries are dialled from the host and skipped). A rule that cannot be added
# leaves that port closed rather than stopping the run.
_DNS_ACCEPT_LINES = [
    "dns() { for p in udp tcp; do \"$IPT\" -w -A OUTPUT -d \"$1/32\" -p $p --dport 53 -j ACCEPT "
    ">/dev/null 2>&1 || true; done; }",
    "while read -r i d g _rest; do",
    '  [ "$i" = Iface ] || [ "$i" = lo ] && continue',
    '  [ "$d" = 00000000 ] && [ "$g" != 00000000 ] && hex8 "$g" && dns "$(ip4 "$g")"',
    "done < /proc/net/route",
    "if [ -r /etc/resolv.conf ]; then",
    "  for a in $(sed -n 's/^# ExtServers: \\[\\(.*\\)\\]$/\\1/p' /etc/resolv.conf 2>/dev/null); do",
    '    case "$a" in ""|*[!0-9.]*) continue;; esac',
    '    dns "$a"',
    "  done",
    "fi",
]

# Rejects every subnet the container is attached to and its default gateway,
# so they hold even when the bridge's subnets could not be read on the host
# and an allowance overlaps them.
_ROUTE_REJECT_LINES = [
    "while read -r i d g _f _c _u _m k _rest; do",
    '  [ "$i" = Iface ] || [ "$i" = lo ] && continue',
    '  hex8 "$d" && hex8 "$g" && hex8 "$k" || fail "unreadable route: $i $d $g $k"',
    '  if [ "$d" = 00000000 ]; then',
    '    [ "$g" = 00000000 ] || r -A OUTPUT -d "$(ip4 "$g")/32" -j REJECT',
    "  else",
    '    r -A OUTPUT -d "$(ip4 "$d")/$(ip4 "$k")" -j REJECT',
    "  fi",
    "done < /proc/net/route",
]

# The IPv4 rules above do not cover IPv6. Where the kernel has IPv6, all IPv6
# output except loopback is dropped with ip6tables. An image without a working
# ip6tables is accepted only if IPv6 is disabled on every interface but lo.
_IPV6_LINES = [
    "if [ -d /proc/sys/net/ipv6/conf ]; then",
    '  IP6=""',
    "  for b in ip6tables ip6tables-legacy; do",
    '    if command -v "$b" >/dev/null 2>&1 && "$b" -w -n -L OUTPUT >/dev/null 2>&1; then IP6="$b"; break; fi',
    "  done",
    '  if [ -n "$IP6" ]; then',
    '    r6() { "$IP6" -w "$@" || fail "ip6tables $*"; }',
    "    r6 -A OUTPUT -o lo -j ACCEPT",
    # REJECT fails a connect at once; the DROP policy holds without it.
    '    "$IP6" -w -A OUTPUT -j REJECT >/dev/null 2>&1 || true',
    "    r6 -P OUTPUT DROP",
    "  else",
    "    for c in /proc/sys/net/ipv6/conf/*; do",
    '      n="${c##*/}"',
    '      case "$n" in all|default|lo) continue;; esac',
    '      v=""; { read -r v < "$c/disable_ipv6"; } 2>/dev/null || true',
    '      [ "$v" = 1 ] || fail "IPv6 is enabled on $n and no working ip6tables in the sandbox image"',
    "    done",
    "  fi",
    "fi",
]


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


def _ipv4_addresses(addresses: Iterable[str]) -> list[str]:
    """IPv4 host addresses; anything else raises (spliced into a root script)."""
    return list(dict.fromkeys(str(ipaddress.IPv4Address(str(a).strip())) for a in addresses))


@functools.lru_cache(maxsize=8)
def _parse_dns_servers(raw: str) -> tuple[str, ...]:
    servers: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            servers.append(str(ipaddress.IPv4Address(item)))
        except ValueError:
            logger.warning("ignoring invalid %s entry %r (one IPv4 address each)", _ENV_DNS_SERVERS, item)
    return tuple(dict.fromkeys(servers))


def dns_servers_from_env() -> tuple[str, ...]:
    """``CLOUD_DNS_SERVERS`` plus the valid addresses in ``SANDBOX_EGRESS_DNS_SERVERS``."""
    extra = _parse_dns_servers(os.environ.get(_ENV_DNS_SERVERS) or "")
    return tuple(dict.fromkeys((*CLOUD_DNS_SERVERS, *extra)))


def build_firewall_script(
    network_cidrs: Iterable[str],
    allowed_cidrs: Iterable[str] = (),
    run_as: str = "sandbox",
    *,
    token: str,
    dns_servers: Iterable[str] | None = None,
) -> str:
    """``sh -c`` script: install the rules, then exec ``"$@"`` as ``run_as``.

    Rule order (first match wins):

    1. loopback is accepted: Docker's embedded DNS (127.0.0.11) is reached
       through it;
    1b. DNS (udp and tcp port 53, nothing else) is accepted to ``dns_servers``
       (default ``dns_servers_from_env()``), the default gateway and the
       resolvers Docker forwards to from the container, so name resolution
       works when Docker forwards from the container's namespace;
    2. link-local (cloud metadata), Azure's wireserver, the bridge's own
       subnets (``network_cidrs``) and, read from the container's routing
       table at start, every subnet it is attached to and its default
       gateway (the host, or under Docker-in-Docker the application pod)
       are rejected;
    3. ``allowed_cidrs`` are accepted;
    4. every other private, CGNAT and reserved range is rejected;
    5. all IPv6 output but loopback is dropped (see ``_IPV6_LINES``).

    An unreadable routing table fails the setup rather than skipping step 2's
    route rejects.

    So an operator allowance, however broad (``0.0.0.0/0``), opens private
    hosts beyond the bridge but never the gateway, sibling sandboxes or the
    metadata endpoints. Blocking the bridge's subnets also covers daemons
    whose address pools sit outside the private ranges.
    """
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("firewall token must be 16-64 lowercase hex characters")
    bridge = list(dict.fromkeys(_ipv4_cidrs(network_cidrs)))
    blocked = [c for c in dict.fromkeys(BLOCKED_CIDRS) if c not in bridge]
    allowed = _ipv4_cidrs(allowed_cidrs)
    resolvers = _ipv4_addresses(dns_servers_from_env() if dns_servers is None else dns_servers)
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
    lines += _ROUTE_HELPER_LINES
    lines += [
        f"r -A OUTPUT -d {ip}/32 -p {proto} --dport 53 -j ACCEPT"
        for ip in resolvers for proto in ("udp", "tcp")
    ]
    lines += _DNS_ACCEPT_LINES
    lines += [f"r -A OUTPUT -d {cidr} -j REJECT" for cidr in _NEVER_ALLOWED_CIDRS]
    lines += [f"r -A OUTPUT -d {cidr} -j REJECT" for cidr in bridge]
    lines += _ROUTE_REJECT_LINES
    lines += [f"r -A OUTPUT -d {cidr} -j ACCEPT" for cidr in allowed]
    lines += [f"r -A OUTPUT -d {cidr} -j REJECT" for cidr in blocked]
    lines += _IPV6_LINES
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


# `timeout -s KILL` ends the program (and, through its process group, any
# children it started) after N seconds even if nothing on the host is left to
# do it, e.g. after the worker that started the run crashed. An image without
# `timeout` still runs the command; the Docker proxy's reaper is then the
# only backstop for an orphaned container.
_DEADLINE_SCRIPT = (
    "s=$1; shift\n"
    'if command -v timeout >/dev/null 2>&1; then exec timeout -s KILL "$s" "$@"; fi\n'
    'exec "$@"\n'
)


def deadline_command(command: list[str], seconds: int) -> list[str]:
    """``command`` wrapped so it is SIGKILLed after ``seconds`` from inside
    the container. Composes with ``firewalled_container_kwargs``: passed as
    its ``command``, the deadline starts after the privilege drop."""
    return ["sh", "-c", _DEADLINE_SCRIPT, "sandbox-deadline", str(int(seconds)), *command]


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


class EgressNetworkUnsafe(RuntimeError):
    """The egress network allows inter-container traffic or IPv6 and could
    not be replaced; a networked run must not join it."""


def _network_problems(attrs: dict[str, Any]) -> list[str]:
    problems = []
    if (attrs.get("Options") or {}).get(_ICC_OPTION) != "false":
        problems.append("allows inter-container traffic")
    if attrs.get("EnableIPv6"):
        problems.append("has IPv6 enabled")
    return problems


def ensure_egress_network_sync(client: Any, network_name: str, labels: dict[str, str]) -> list[str]:
    """Create the egress bridge with inter-container traffic and IPv6 off and
    return its IPv4 subnets (empty if they can't be read).

    An existing network that allows inter-container traffic or has IPv6 on
    is replaced while idle. When it cannot be replaced (containers are
    attached, or the Docker proxy refuses to remove a network another stack
    created) ``EgressNetworkUnsafe`` is raised: sandboxes on it would be
    reachable from unfirewalled peers, or have IPv6 the firewall's IPv4
    rules do not cover.
    """
    network = _get_network(client, network_name)
    problems = _network_problems(network.attrs) if network is not None else []
    if network is not None and problems:
        what = " and ".join(problems)
        if network.attrs.get("Containers"):
            raise EgressNetworkUnsafe(
                f"sandbox egress network {network_name} {what} and has containers attached, so it "
                "cannot be recreated; networked sandbox runs are refused until it is removed "
                "(or set a different egress network name)"
            )
        logger.info("recreating sandbox egress network %s: it %s", network_name, what)
        try:
            network.remove()
        except Exception as exc:
            # In use by containers this client is not shown, or not created
            # by this stack's Docker proxy (which only removes its own).
            raise EgressNetworkUnsafe(
                f"sandbox egress network {network_name} {what} and could not be recreated ({exc}); "
                "networked sandbox runs are refused until it is removed "
                "(or set a different egress network name)"
            ) from exc
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
        problems = _network_problems(network.attrs) if network is not None else []
        if problems:
            # A daemon default (default-network-opts) can turn IPv6 on, or a
            # racing creator made it differently.
            raise EgressNetworkUnsafe(
                f"sandbox egress network {network_name} {' and '.join(problems)} as created on "
                "this Docker daemon; networked sandbox runs are refused (check the daemon's "
                "default-network-opts)"
            )
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
