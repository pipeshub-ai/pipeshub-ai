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
# The rules every firewall script installs, as egress_firewall's _rule_lines writes them; None is the fail() line,
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
_FIXED_HEAD: Final = (*_RULES_HEAD[:2], "command -v setpriv >/dev/null 2>&1 || fail 'setpriv not found'",
                      *_RULES_HEAD[2:])
_RULES_EXIT: Final = "exit 0"
_FAIL: Final = re.compile(
    r"""fail\(\) \{ echo '\[sandbox-egress\] firewall unavailable \([0-9a-f]{16,64}\):' "\$1" >&2; exit \d{1,3}; \}"""
)
_RULE: Final = re.compile(rf"r -A OUTPUT -d {_CIDR} -j (?:ACCEPT|REJECT)")
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


def _head_and_rules_ok(head: list[str], expected: tuple[str | None, ...], rules: list[str]) -> bool:
    if len(head) != len(expected):
        return False
    for line, want in zip(head, expected, strict=True):
        if (want is None and not _FAIL.fullmatch(line)) or (want is not None and line != want):
            return False
    for line in rules:
        match = _RULE.fullmatch(line)
        if match is None or not _ipv4_network(match["cidr"]):
            return False
    return True


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


def _ipv4_network(text: str) -> bool:
    try:
        return ipaddress.ip_network(text, strict=True).version == 4
    except ValueError:
        return False


__all__ = [
    "FIREWALL_SETUP_CAPS",
    "LIMITED",
    "NO_NEW_PRIVILEGES",
    "firewall_install",
    "firewall_rules_script_ok",
    "firewall_script_ok",
    "floor_violations",
    "root_user",
]
