"""The isolation floor every sandbox container has, whoever asks the daemon for it (SEC-A-04).

Two processes guard the Docker socket: the sandbox broker (``app.harness.sandbox.docker_policy``) and, on installs
still moving off ``SANDBOX_MODE=docker``, the legacy socket proxy (``app.sandbox.docker_proxy``). Each used to keep
its own rules, and the legacy one only listed what a container must not have, so a create with no pids limit, a
writable root and Docker's default capabilities went through. Both now check this one floor:

- every capability dropped, ``no-new-privileges``;
- a read-only root filesystem;
- a pids limit, a memory limit equal to memory plus swap (no swap), and a CPU limit;
- a user that is not root.

The one exception is the legacy egress firewall's install step (``firewall_install``): root with the capabilities
that install the rules, running the firewall script and nothing else, which drops to the sandbox user before the
command runs. The script is checked line by line against the grammar ``egress_firewall`` builds, so a create cannot
put its own commands before the drop. The broker's firewall helper, root with ``NET_ADMIN`` in a sandbox's network
namespace, runs the rules part of the same grammar and exits (``firewall_rules_script_ok``, R43SOCK-FU-4).

Stdlib only: the legacy proxy imports nothing else.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

NO_NEW_PRIVILEGES: Final = "no-new-privileges:true"
LIMITED: Final = ("PidsLimit", "Memory", "NanoCpus")
# What the legacy firewall's install step may add: NET_ADMIN installs the rules, SETUID/SETGID/SETPCAP let setpriv
# switch to the sandbox user and empty the bounding set. All are gone before the command runs.
FIREWALL_SETUP_CAPS: Final = frozenset({"NET_ADMIN", "SETUID", "SETGID", "SETPCAP"})
FIREWALL_ARGV0: Final = "sandbox-egress"

_USER: Final = r"[a-z_][a-z0-9_-]{0,31}"
_CIDR: Final = r"(?P<cidr>\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})"
# The head every firewall script starts with, as egress_firewall's _rule_lines writes it; None is the fail() line,
# with the run's token. The legacy script also checks for setpriv (third line) and ends by dropping to the sandbox
# user; the broker's helper (build_firewall_rules_script) installs the rules and exits.
_RULES_HEAD: Final = (
    "set -u",
    None,
    'IPT=""',
    "for b in iptables iptables-legacy; do",
    '  if command -v "$b" >/dev/null 2>&1 && "$b" -w -n -L OUTPUT >/dev/null 2>&1; then IPT="$b"; break; fi',
    "done",
    "[ -n \"$IPT\" ] || fail 'no working iptables in the sandbox image'",
    'r() { "$IPT" -w "$@" || fail "iptables $*"; }',
    "r -A OUTPUT -o lo -j ACCEPT",
)

# Helpers for /proc/net/route (little-endian hex). Fields: Iface Destination
# Gateway Flags RefCnt Use Metric Mask.
ROUTE_HELPER_LINES: Final = (
    "ip4() { h=$1; printf '%d.%d.%d.%d' \"0x${h#??????}\" \"0x$(echo \"$h\" | cut -c5-6)\" "
    "\"0x$(echo \"$h\" | cut -c3-4)\" \"0x$(echo \"$h\" | cut -c1-2)\"; }",
    "hex8() { case $1 in [0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]) "
    "return 0;; esac; return 1; }",
    "[ -r /proc/net/route ] || fail 'cannot read /proc/net/route'",
)

# Opens port 53 (udp and tcp) on the default gateway and on every IPv4 server
# Docker's resolv.conf lists as forwarded from the container (``host(...)``
# entries are dialled from the host and skipped). A rule that cannot be added
# leaves that port closed rather than stopping the run.
DNS_ACCEPT_LINES: Final = (
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
)

# Rejects every subnet the container is attached to and its default gateway,
# so they hold even when the bridge's subnets could not be read on the host
# and an allowance overlaps them.
ROUTE_REJECT_LINES: Final = (
    "while read -r i d g _f _c _u _m k _rest; do",
    '  [ "$i" = Iface ] || [ "$i" = lo ] && continue',
    '  hex8 "$d" && hex8 "$g" && hex8 "$k" || fail "unreadable route: $i $d $g $k"',
    '  if [ "$d" = 00000000 ]; then',
    '    [ "$g" = 00000000 ] || r -A OUTPUT -d "$(ip4 "$g")/32" -j REJECT',
    "  else",
    '    r -A OUTPUT -d "$(ip4 "$d")/$(ip4 "$k")" -j REJECT',
    "  fi",
    "done < /proc/net/route",
)

# The IPv4 rules above do not cover IPv6. Where the kernel has IPv6, all IPv6
# output except loopback is dropped with ip6tables. An image without a working
# ip6tables is accepted only if IPv6 is disabled on every interface but lo.
IPV6_LINES: Final = (
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
)
_FIXED_HEAD: Final = (*_RULES_HEAD[:2], "command -v setpriv >/dev/null 2>&1 || fail 'setpriv not found'",
                      *_RULES_HEAD[2:])
_RULES_EXIT: Final = "exit 0"
_FAIL: Final = re.compile(
    r"""fail\(\) \{ echo '\[sandbox-egress\] firewall unavailable \([0-9a-f]{16,64}\):' "\$1" >&2; exit \d{1,3}; \}"""
)
_RULE: Final = re.compile(rf"r -A OUTPUT -d {_CIDR} -j (?:ACCEPT|REJECT)")
_DNS_RULE: Final = re.compile(r"r -A OUTPUT -d (?P<ip>\d{1,3}(?:\.\d{1,3}){3})/32 -p (?:udp|tcp) --dport 53 -j ACCEPT")
_TAIL: Final = (
    re.compile(rf'h="\$\(getent passwd (?P<u1>{_USER}) \| cut -d: -f6\)" \|\| true'),
    re.compile(r'\[ -n "\$h" \] && export HOME="\$h"'),
    re.compile(rf"export USER=(?P<u2>{_USER}) LOGNAME=(?P<u3>{_USER})"),
    re.compile(
        rf"exec setpriv --reuid=(?P<u4>{_USER}) --regid=(?P<u5>{_USER}) --init-groups "
        r'--inh-caps=-all --bounding-set=-all --no-new-privs -- "\$@"'
    ),
)


def root_user(user: object) -> bool:
    """Whether a container's ``User`` runs as root: unset (the image's own user, root for most) included."""
    return not isinstance(user, str) or user.split(":")[0] in ("", "0", "root")


def floor_violations(host_config: Mapping[str, object]) -> list[str]:
    """What ``host_config`` lacks of the floor; empty when it has all of it. The user is checked by the caller,
    which knows the one container that may start as root."""
    found: list[str] = []
    if host_config.get("CapDrop") != ["ALL"]:
        found.append("CapDrop is not [ALL]")
    if host_config.get("SecurityOpt") != [NO_NEW_PRIVILEGES]:
        found.append("SecurityOpt is not exactly no-new-privileges")
    if host_config.get("ReadonlyRootfs") is not True:
        found.append("root filesystem is writable")
    for key in LIMITED:
        value = host_config.get(key)
        if type(value) is not int or value <= 0:
            found.append(f"{key} is not limited")
    if host_config.get("MemorySwap") != host_config.get("Memory"):
        found.append("swap is not disabled")
    return found


def _lines(script: str) -> list[str]:
    lines = script.split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _cidr_rules_ok(rules: list[str]) -> bool:
    for line in rules:
        match = _RULE.fullmatch(line)
        if match is None or not _ipv4_network(match["cidr"]):
            return False
    return True


def _take_block(lines: list[str], block: tuple[str, ...]) -> list[str] | None:
    """``lines`` after ``block`` when they start with it exactly, else None."""
    return lines[len(block) :] if tuple(lines[: len(block)]) == block else None


def _take_until(lines: list[str], block: tuple[str, ...]) -> tuple[list[str], list[str]] | None:
    """The lines before the first occurrence of ``block`` and the lines after it; None when it is absent."""
    for i in range(len(lines) - len(block) + 1):
        if tuple(lines[i : i + len(block)]) == block:
            return lines[:i], lines[i + len(block) :]
    return None


def _head_and_rules_ok(head: list[str], expected: tuple[str | None, ...], rules: list[str]) -> bool:
    """The fixed head, then the rule body exactly as ``egress_firewall._rule_lines`` orders it: the route helpers,
    port-53 accepts on single IPv4 resolvers, the DNS accept block, IPv4 rules, the route rejects, IPv4 rules and
    the IPv6 block."""
    if len(head) != len(expected):
        return False
    for line, want in zip(head, expected, strict=True):
        if (want is None and not _FAIL.fullmatch(line)) or (want is not None and line != want):
            return False
    rest = _take_block(rules, ROUTE_HELPER_LINES)
    if rest is None:
        return False
    split = _take_until(rest, DNS_ACCEPT_LINES)
    if split is None:
        return False
    resolvers, rest = split
    for line in resolvers:
        match = _DNS_RULE.fullmatch(line)
        if match is None or not _ipv4_address(match["ip"]):
            return False
    split = _take_until(rest, ROUTE_REJECT_LINES)
    if split is None:
        return False
    before, rest = split
    if len(rest) < len(IPV6_LINES) or tuple(rest[-len(IPV6_LINES) :]) != IPV6_LINES:
        return False
    return _cidr_rules_ok(before) and _cidr_rules_ok(rest[: -len(IPV6_LINES)])

def firewall_script_ok(script: str) -> bool:
    """Whether ``script`` is the legacy egress firewall exactly as ``egress_firewall.build_firewall_script`` writes
    it: fixed lines, IPv4 rules only, and a drop to one non-root user before ``"$@"`` runs."""
    lines = _lines(script)
    head, rest = lines[: len(_FIXED_HEAD)], lines[len(_FIXED_HEAD) :]
    if len(rest) < len(_TAIL):
        return False
    rules, tail = rest[: -len(_TAIL)], rest[-len(_TAIL) :]
    if not _head_and_rules_ok(head, _FIXED_HEAD, rules):
        return False
    users: set[str] = set()
    for line, pattern in zip(tail, _TAIL, strict=True):
        match = pattern.fullmatch(line)
        if match is None:
            return False
        users.update(v for v in match.groupdict().values() if v is not None)
    return len(users) == 1 and not root_user(next(iter(users)))


def firewall_rules_script_ok(script: str) -> bool:
    """Whether ``script`` is the broker's egress firewall helper exactly as
    ``egress_firewall.build_firewall_rules_script`` writes it: the same fixed lines and IPv4 rules as the legacy
    script, then ``exit 0`` and nothing else (R43SOCK-FU-4). The helper runs as root with ``NET_ADMIN`` in a
    sandbox's network namespace, so a command of its caller's own must not get in."""
    lines = _lines(script)
    if not lines or lines[-1] != _RULES_EXIT:
        return False
    lines = lines[:-1]
    return _head_and_rules_ok(lines[: len(_RULES_HEAD)], _RULES_HEAD, lines[len(_RULES_HEAD) :])


def firewall_install(user: object, command: object, cap_add: Sequence[str]) -> bool:
    """Whether a create is the legacy egress firewall's install step: root, only the firewall's capabilities, and the
    firewall script ahead of the command it hands to the sandbox user."""
    if user != "0" or not set(cap_add) <= FIREWALL_SETUP_CAPS:
        return False
    if not isinstance(command, list) or len(command) < 4:
        return False
    argv = [str(part) for part in command]
    return argv[:2] == ["sh", "-c"] and argv[3] == FIREWALL_ARGV0 and firewall_script_ok(argv[2])


def _ipv4_address(text: str) -> bool:
    try:
        return ipaddress.ip_address(text).version == 4
    except ValueError:
        return False


def _ipv4_network(text: str) -> bool:
    try:
        return ipaddress.ip_network(text, strict=True).version == 4
    except ValueError:
        return False


__all__ = [
    "DNS_ACCEPT_LINES",
    "FIREWALL_SETUP_CAPS",
    "IPV6_LINES",
    "LIMITED",
    "NO_NEW_PRIVILEGES",
    "ROUTE_HELPER_LINES",
    "ROUTE_REJECT_LINES",
    "firewall_install",
    "firewall_rules_script_ok",
    "firewall_script_ok",
    "floor_violations",
    "root_user",
]
