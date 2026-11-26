"""R31-SECKEY: what an engine sandbox holds and reaches, read from inside it while its job runs.

Imported by seckey.py on the host (no third-party package): the bash a check runs in the sandbox, and the pure readers
of what it printed. tests/unit/harness/test_r31seckey_sandbox.py holds the readers to fixed outputs.

Every check runs through ``docker exec`` into the run's live sandbox, so it sees what the engine sees: gVisor keeps a
sandbox's own writes (its tmpfs, its overlay) inside the sandbox, where no read from the host reaches them.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/python"))
from harness_core.testing.canary import encoded_forms

# The scan reads the sandbox out as tar streams and searches them on the host with GNU grep -F (every form of every
# canary at once): busybox grep in the engine images takes minutes per pattern set on gVisor, the host's grep seconds.
# Two readers, because the sandbox drops every capability: root reads all it owns but not the run user's own 0700
# directories, the run user (uid 1000) reads those. The kernel's filesystems (proc, sys, dev) are left out; every
# process's environment and command line come apart, as text (PROCS).
ROOT_TAR = r"""
exec 2>/dev/null
set --
for d in /* /.[!.]*; do
  case "$d" in /proc|/sys|/dev|'/.[!.]*') ;; *) [ -e "$d" ] && set -- "$@" "$d" ;; esac
done
tar -cf - "$@"
"""
USER_TAR = r"""
exec 2>/dev/null
set --
for d in /workspace /state /skills /tmp /home /pipeshub /pxh-sealed; do [ -e "$d" ] && set -- "$@" "$d"; done
tar -cf - "$@"
"""
PROCS = r"""
exec 2>/dev/null
for f in /proc/[0-9]*/environ; do echo "== ENV $f"; tr '\0' '\n' < "$f"; echo; done
for f in /proc/[0-9]*/cmdline; do echo "== CMD $f"; tr '\0' ' ' < "$f"; echo; done
echo "== PROCS_DONE"
"""
# The run's own files the shape scan reads (the image's files hold documentation examples of every shape).
OWN_PLACES = ("workspace", "state", "skills", "tmp", "home", "root", "pipeshub", "pxh-sealed")


# Writes one sentinel per location the scan must cover, in every encoded form, and removes them with ``clean``.
SENTINEL_PLACES = ("/tmp", "/state", "/workspace")


def sentinel_plant(sentinel: str) -> str:
    body = " ".join(f"'{value}'" for value in encoded_forms(sentinel).values())
    return "exec 2>/dev/null\n" + "\n".join(
        f"printf '%s\\n' {body} > {place}/.seckey-sentinel && echo PLANTED {place}" for place in SENTINEL_PLACES)


def sentinel_clean() -> str:
    return "exec 2>/dev/null\n" + "\n".join(f"rm -f {place}/.seckey-sentinel" for place in SENTINEL_PLACES)


@dataclass(frozen=True)
class Found:
    name: str  # the canary's name (seckey_inventory), never its value
    form: str  # raw, base64.N, url, hex...
    path: str


@dataclass
class ScanResult:
    found: list[Found] = field(default_factory=list)
    complete: bool = False
    sentinel_seen: set[str] = field(default_factory=set)

    def leaks(self) -> list[Found]:
        return [f for f in self.found if f.name != SENTINEL]


SENTINEL = "sentinel"


def patterns(canaries: dict[str, str], sentinel: str) -> tuple[str, dict[str, tuple[str, str]]]:
    """grep's pattern list (stdin) and what each pattern is: (canary name, form)."""
    table: dict[str, tuple[str, str]] = {}
    for name, value in [*canaries.items(), (SENTINEL, sentinel)]:
        for form, text in encoded_forms(value).items():
            # A short stable base64 tail would match noise; every canary is long, so this drops nothing real.
            if len(text) >= 12:
                table.setdefault(text, (name, form))
    return "\n".join(table) + "\n", table


def read_scan(out: str, table: dict[str, tuple[str, str]], base: str = "") -> ScanResult:
    """GNU grep -rHaoF's ``path:match`` lines over the extracted sandbox (``base``: the host directory, whose
    ``root/``, ``user/`` and ``proc/`` parts become the sandbox's paths). A match is one of the patterns, so a line
    splits at the ``:`` before it. ``SCAN_DONE <grep's status>`` ends a finished scan."""
    result = ScanResult()
    for line in out.splitlines():
        if line.startswith("SCAN_DONE "):
            # grep: 0 a match, 1 none, 2 an unreadable file among many (still a full scan of the rest).
            result.complete = line.split()[1] in ("0", "1", "2")
            continue
        for text, (name, form) in table.items():
            if line.endswith(":" + text):
                path = sandbox_path(line[: -len(text) - 1], base)
                if name == SENTINEL:
                    result.sentinel_seen.add(_place(path))
                result.found.append(Found(name, form, path))
                break
    return result


def sandbox_path(path: str, base: str) -> str:
    """The sandbox's own path of a file the host extracted under ``base``."""
    rest = path[len(base):].lstrip("/") if base and path.startswith(base) else path.lstrip("/")
    head, _, tail = rest.partition("/")
    if head == "proc":
        pid, _, kind = tail.partition(".")
        return f"/proc/{pid}/{kind}"
    return "/" + tail if head in ("root", "user") else "/" + rest


def _place(path: str) -> str:
    if path.startswith("/proc/"):
        return "environ" if path.endswith("/environ") else "cmdline"
    return "/" + path.strip("/").split("/", 1)[0]


_ENV_HEAD = re.compile(r"^== ENV /proc/(\d+)/environ$")
# A value a credential-named variable may hold in a sandbox: a placeholder the proxy swaps (``pxh-ph-``, the tools and
# model placeholders), a path or an address, or a word.
PLACEHOLDER = re.compile(r"^(pxh-[a-z]+-[A-Za-z0-9_-]{8,}|pxh-ph-[0-9a-f]{32})$")
NOT_A_SECRET = re.compile(r"^(|[0-9]+|true|false|none|null|[a-z_]+|/[^ ]*|https?://[^ ]*|[A-Za-z0-9._-]+:[0-9]+)$")
CREDENTIAL_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE)
PLACEHOLDER_IN_TEXT = re.compile(r"pxh-(?:ph-[0-9a-f]{32}|[a-z]+-[A-Za-z0-9_-]{8,})")


def environments(dump: str) -> dict[str, dict[str, str]]:
    """pid -> its environment, from PROCS's ``== ENV`` sections."""
    out: dict[str, dict[str, str]] = {}
    pid = None
    for line in dump.splitlines():
        if line.startswith("== "):
            match = _ENV_HEAD.match(line)
            pid = match.group(1) if match else None
            if pid is not None:
                # Two readers read every process: one that may not read it adds nothing.
                out.setdefault(pid, {})
            continue
        if pid is not None and "=" in line:
            key, value = line.split("=", 1)
            out[pid][key] = value
    return out


def credential_variables(envs: dict[str, dict[str, str]], image_env: dict[str, str] | None = None,
                         ignore: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """(the credential-named variables that hold a placeholder, those that hold anything else): ``NAME`` only. A
    variable the engine's image itself sets to that value (``image_env``: a base image's public ``GPG_KEY``) is the
    image's, not the run's; ``ignore`` names the lane's own."""
    placeholders: set[str] = set()
    other: set[str] = set()
    for env in envs.values():
        for key, value in env.items():
            if not CREDENTIAL_NAME.search(key) or key in ignore or (image_env or {}).get(key) == value:
                continue
            if PLACEHOLDER.match(value):
                placeholders.add(key)
            elif not NOT_A_SECRET.match(value):
                other.add(key)
    return sorted(placeholders), sorted(other)


# --- direct paths out (gap 2) ----------------------------------------------------------------------------------------

DIRECT = r"""
exec 2>/dev/null
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy
dns() { local a; a=$(timeout 5 getent hosts "$1" | awk '{print $1}' | sort -u | tr '\n' ','); echo "DNS $1 ${a:-unresolved}"; }
tcp() { if timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2"; then echo "TCP $1 $2 open"; else echo "TCP $1 $2 closed"; fi; }
# A sandbox has a small process limit: a few probes at a time.
n=0
bg() { "$@" & n=$((n + 1)); if [ "$n" -ge 6 ]; then wait; n=0; fi; }
@NAMES@
@ADDRESSES@
wait
echo "DIRECT_DONE"
"""

# Through the egress proxy with the sandbox's own placeholder: an absolute-form request (plain HTTP) and a CONNECT
# (TLS), as an HTTP client configured with the proxy sends them. ``$P``: the proxy's address and port.
VIA_PROXY = r"""
exec 2>/dev/null
P="$1"; TOK="$2"; MTOK="$3"
ask() {
  local out request="${2//\$MTOK/$MTOK}"
  request="${request//\$TOK/$TOK}"
  out=$( (exec 3<>"/dev/tcp/${P%:*}/${P#*:}" || exit 0; printf '%b' "$request" >&3; timeout 8 cat <&3) | tr -d '\r' | head -c 600 | tr '\n' ' ')
  echo "PROXY $1 ${out:-silent}"
}
@ASKS@
echo "PROXY_DONE"
"""


# A JSON-RPC call to the Tool Gateway as the engine makes it: the proxy's port under the gateway's name, the sandbox's
# tools placeholder as the bearer. ``$1`` the proxy, ``$2`` the bearer, ``$3`` the body; the whole answer is printed.
# HTTP/1.0, so a long answer is never chunked across the JSON it carries.
RPC = r"""
exec 2>/dev/null
P="$1"; TOK="$2"; BODY="$3"
exec 3<>"/dev/tcp/${P%:*}/${P#*:}" || { echo "RPC_CONNECT_FAILED"; exit 0; }
printf 'POST /mcp HTTP/1.1\r\nHost: tool-gateway:8097\r\nAuthorization: Bearer %s\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nContent-Length: %s\r\nConnection: close\r\n\r\n%s' "$TOK" "${#BODY}" "$BODY" >&3
timeout 20 cat <&3
echo
echo "RPC_DONE"
"""


def rpc_body(method: str, params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, separators=(",", ":"))


def _dechunk(body: str) -> str:
    """A chunked body's content: chunk sizes count bytes, so it is cut as bytes."""
    data, out = body.encode(), bytearray()
    while data:
        size_line, _, data = data.partition(b"\r\n")
        try:
            size = int(size_line.strip().split(b";")[0] or b"0", 16)
        except ValueError:
            return body
        if size == 0:
            break
        out += data[:size]
        data = data[size + 2:]
    return out.decode("utf-8", "replace")


def read_rpc(out: str) -> tuple[int | None, dict[str, Any]]:
    """(the HTTP status, the JSON-RPC answer: the body, or the ``data:`` of a server-sent event). The proxy answers
    HTTP/1.1 and may chunk the body whatever the request asked: it is unchunked first."""
    match = _STATUS.search(out)
    head, _, body = out.partition("\r\n\r\n")
    if "transfer-encoding: chunked" in head.lower():
        out = head + "\r\n\r\n" + _dechunk(body)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line.startswith("{"):
            try:
                return (int(match.group(1)) if match else None), json.loads(line)
            except ValueError:
                continue
    return (int(match.group(1)) if match else None), {}


def rpc_ran(status: int | None, answer: dict[str, Any]) -> bool:
    """Whether a ``tools/call`` ran: a 200 with a result that is not an error."""
    result = answer.get("result") or {}
    return status == 200 and "error" not in answer and not result.get("isError")


def direct_script(names: list[str], addresses: list[tuple[str, int]]) -> str:
    return (DIRECT.replace("@NAMES@", "\n".join(f"bg dns {n}" for n in names))
            .replace("@ADDRESSES@", "\n".join(f"bg tcp {h} {p}" for h, p in addresses)))


def via_proxy_script(asks: dict[str, str]) -> str:
    return VIA_PROXY.replace("@ASKS@", "\n".join(f"ask {label} '{request}'" for label, request in asks.items()))


def absolute(url: str, host: str, token_header: str = "Authorization: Bearer $TOK") -> str:
    return f"GET {url} HTTP/1.1\\r\\nHost: {host}\\r\\n{token_header}\\r\\nConnection: close\\r\\n\\r\\n"


def connect(host: str, port: int) -> str:
    return f"CONNECT {host}:{port} HTTP/1.1\\r\\nHost: {host}:{port}\\r\\nProxy-Authorization: Bearer $TOK\\r\\n\\r\\n"


def read_direct(out: str) -> tuple[dict[str, list[str]], dict[tuple[str, int], bool], bool]:
    """(name -> the addresses it resolved to, [] when it did not; (host, port) -> open; finished)."""
    names: dict[str, list[str]] = {}
    ports: dict[tuple[str, int], bool] = {}
    for line in out.splitlines():
        parts = line.split()
        if parts[:1] == ["DNS"] and len(parts) == 3:
            names[parts[1]] = [] if parts[2] == "unresolved" else [a for a in parts[2].split(",") if a]
        elif parts[:1] == ["TCP"] and len(parts) == 4:
            ports[(parts[1], int(parts[2]))] = parts[3] == "open"
    return names, ports, "DIRECT_DONE" in out


_STATUS = re.compile(r"HTTP/1\.[01] (\d{3})")


def read_proxy(out: str) -> dict[str, tuple[int | None, str]]:
    """label -> (status, the first 300 characters of the answer)."""
    answers: dict[str, tuple[int | None, str]] = {}
    for line in out.splitlines():
        if line.startswith("PROXY ") and line != "PROXY_DONE":
            _, label, rest = (line.split(" ", 2) + [""])[:3]
            match = _STATUS.search(rest)
            answers[label] = (int(match.group(1)) if match else None, rest[:300])
    return answers


def refused(answer: tuple[int | None, str]) -> bool:
    """A proxy refusal (403 with the proxy's reason) or no answer at all; never a 2xx/3xx from an upstream."""
    status, text = answer
    return status is None or (status in (400, 403, 405, 407) and "upstream" not in text.lower()) or status == 502


def json_lines(out: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                found.append(json.loads(line))
            except ValueError:
                continue
    return found


__all__ = [
    "OWN_PLACES",
    "PROCS",
    "ROOT_TAR",
    "RPC",
    "SENTINEL",
    "SENTINEL_PLACES",
    "USER_TAR",
    "Found",
    "ScanResult",
    "absolute",
    "connect",
    "credential_variables",
    "direct_script",
    "environments",
    "json_lines",
    "patterns",
    "read_direct",
    "read_proxy",
    "read_rpc",
    "read_scan",
    "refused",
    "rpc_body",
    "rpc_ran",
    "sandbox_path",
    "sentinel_clean",
    "sentinel_plant",
    "via_proxy_script",
]


def proc_sections(dump: str) -> dict[str, str]:
    """PROCS's output as files the host scan reads: ``<pid>.environ`` and ``<pid>.cmdline`` -> their text."""
    files: dict[str, str] = {}
    current = None
    for line in dump.splitlines():
        if line.startswith(("== ENV /proc/", "== CMD /proc/")):
            pid = line.split("/")[2]
            current = f"{pid}.{'environ' if line.startswith('== ENV') else 'cmdline'}"
            files[current] = ""
        elif line.startswith("== "):
            current = None
        elif current is not None:
            files[current] += line + "\n"
    return files
