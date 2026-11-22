"""The child process of the image materialiser: fetch the referenced images and splice them into the stored file.

This module is loaded by a spawned process that holds a user's request data and a storage token, so it
imports only the standard library, ``aiohttp`` and ``yarl`` (a test reads this file's imports). Nothing
from the application is imported: the parent hands over everything as plain data and gets plain data back.

What the child enforces, each by running totals on bytes actually read or written (a lying ``info`` or a
store that ignores a header cannot get past them):

* one image at most 20 MiB (aborted at byte 20 MiB + 1), the distinct images together at most 36 MiB, so
  the temp files stay below 36 MiB;
* the output at most ``cap_bytes`` (the effective inlined cap) and, by ``RLIMIT_FSIZE``, 49 MiB;
* downloads are bound to the verified object: ``?ifMatch=`` for local storage, ``If-Match`` on the signed
  hop for S3 and Azure, the size ``info`` verified, and the response's ``ETag``;
* the signed URL is pinned to the document's own stored object URL (see ``check_signed_url``);
* the bytes must start like a PNG, JPEG, GIF or WebP file; the subtype comes from the bytes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
from typing import Any

import aiohttp
import yarl

MAX_IMAGE_BYTES = 20 << 20
MAX_DISTINCT_BYTES = 36 << 20
FSIZE_LIMIT = 49 << 20
ADDRESS_SPACE_HEADROOM = 512 << 20
SIGNED_URL_JSON_MAX = 8 << 10
CHUNK = 3 * 64 * 1024  # a multiple of 3, so base64 of the chunks concatenates to base64 of the whole
LOG_PREFIX = "signed-url pin:"

MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF",),
}


class Refused(Exception):
    """A request the child refuses; the parent turns it into the push's answer."""

    def __init__(self, status: int, reason: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status, self.reason, self.message, self.extra = status, reason, message, extra

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "message": self.message, "extra": self.extra}


def fetch_failed(message: str) -> Refused:
    return Refused(422, "IMAGE_FETCH_FAILED", message)


#: ENOSPC and EDQUOT: the spool is full, which is the server's fault.
DISK_FULL = (28, 122)


def spool_full() -> Refused:
    return Refused(503, "SPOOL_FULL", "The server has no space to hold the request.", retryAfter=5)


def too_large(cap: int) -> Refused:
    return Refused(413, "PAYLOAD_TOO_LARGE", "The record is too large once its images are inlined.", limit="resolved", limitBytes=cap)


def sniff(head: bytes) -> str | None:
    """The image subtype whose magic number ``head`` starts with (WebP also needs ``WEBP`` at byte 8)."""
    for subtype, magics in MAGIC.items():
        if head.startswith(magics) and (subtype != "webp" or head[8:12] == b"WEBP"):
            return subtype
    return None


# ---- the signed-URL pin ---------------------------------------------------------------------------

_LISTED = "listed"


def _is_public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _is_literal_or_local(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _origin(url: yarl.URL) -> tuple[str, str, int]:
    return (url.scheme, (url.host or "").lower(), url.port or 0)


def _raw_checks(raw: str, what: str) -> yarl.URL:
    """Reject the spellings that parsers disagree about, on the raw text (before any decoding)."""
    if "\\" in raw or any(ord(c) < 0x21 or ord(c) == 0x7F for c in raw):
        raise fetch_failed(f"{what} is malformed")
    authority = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]*)", raw)
    if not authority:
        raise fetch_failed(f"{what} is malformed")
    auth = authority.group(1)
    if "@" in auth or "%" in auth:  # userinfo, zone id, percent-encoded host
        raise fetch_failed(f"{what} is malformed")
    host = re.sub(r":\d*$", "", auth) if not auth.startswith("[") else auth
    if host.endswith(".") or host != host.lower() or not host.isascii():  # trailing dot, upper case, IDNA
        raise fetch_failed(f"{what} is malformed")
    try:
        url = yarl.URL(raw, encoded=True)
    except ValueError:
        raise fetch_failed(f"{what} is malformed") from None
    if not url.host:
        raise fetch_failed(f"{what} is malformed")
    return url


def _decoded_path(url: yarl.URL) -> str:
    return yarl.URL.build(path=url.raw_path, encoded=True).path


def parse_origin_list(entries: list[str], *, allow_loopback_http: bool = False) -> list[tuple[str, str, int]]:
    """Validate the operator's ``KB_PUSH_SIGNED_URL_ORIGINS``: https origins only, no wildcard, path or userinfo.

    Raises ``ValueError``. ``allow_loopback_http`` is the test seam that admits ``http://127.0.0.1:port``."""
    out = []
    for entry in entries:
        if "*" in entry:
            raise ValueError(f"wildcard in signed-url origin {entry!r}")
        url = yarl.URL(entry)
        if url.user or url.password or url.path not in ("", "/") or url.query_string or url.fragment or not url.host:
            raise ValueError(f"signed-url origin {entry!r} must be scheme://host[:port] only")
        loopback = allow_loopback_http and url.host in ("127.0.0.1", "localhost", "::1")
        if url.scheme != "https" and not (url.scheme == "http" and loopback):
            raise ValueError(f"signed-url origin {entry!r} must be https")
        out.append(_origin(url))
    return out


def check_signed_url(signed: str, stored: str | None, listed: list[tuple[str, str, int]], *, allow_loopback: bool = False) -> bool:
    """Whether ``signed`` may be fetched. Raises ``Refused`` (422 IMAGE_FETCH_FAILED) when it may not.

    Returns True when the origin is operator-listed (the DNS check then admits a non-public peer).
    Rules: both URLs are well formed (no backslash, userinfo, zone id, trailing dot, upper-case or IDNA host);
    the stored URL is itself public (no IP literal, localhost, loopback, private or link-local) unless its
    origin is listed or the test seam is on; the signed URL has the stored URL's scheme, host and port and the
    same percent-decoded path; for a listed origin (path-style S3 puts the bucket before the key) the decoded
    path need only end with the stored key path.
    """
    if not stored:
        raise fetch_failed("the stored object has no URL to bind the download to")
    stored_url = _raw_checks(stored, "the stored object URL")
    signed_url = _raw_checks(signed, "the signed URL")
    listed_origin = _origin(signed_url) in listed
    seam = allow_loopback  # the test seam (never set by the production runtime) admits http and a non-public stored host
    if signed_url.scheme != "https" and not (seam and signed_url.scheme == "http"):
        raise fetch_failed("the signed URL is not https")
    if _is_literal_or_local(stored_url.host or "") and not (seam or _origin(stored_url) in listed):
        raise fetch_failed("the stored object URL is not a public host")
    stored_path, signed_path = _decoded_path(stored_url), _decoded_path(signed_url)
    if listed_origin:
        if not signed_path.endswith(stored_path):
            raise fetch_failed("the signed URL does not name the stored object")
    else:
        if _origin(signed_url) != _origin(stored_url) or signed_path != stored_path:
            raise fetch_failed("the signed URL does not name the stored object")
    return listed_origin or seam


class PinnedResolver(aiohttp.abc.AbstractResolver):
    """Resolves with the system resolver and refuses a non-public peer unless the origin is admitted.

    ``override`` is the test seam: a mapping from host to a loopback address, so a test can reach a local server
    under a real-looking host name while TLS and the Host header keep the name."""

    def __init__(self, *, admit_non_public: bool, override: dict[str, str] | None) -> None:
        self._admit = admit_non_public
        self._override = override or {}

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict[str, Any]]:
        target = _override_for(self._override, host)
        if target is not None:
            addresses, port = [target[0]], target[1] or port
        else:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            addresses = sorted({str(i[4][0]) for i in infos})
        if not addresses:
            raise OSError("no address")
        if not self._admit and not all(_is_public_ip(a) for a in addresses):
            raise fetch_failed("the signed URL resolves to a non-public address")
        return [
            {"hostname": host, "host": a, "port": port, "family": socket.AF_INET6 if ":" in a else socket.AF_INET, "proto": 0, "flags": socket.AI_NUMERICHOST}
            for a in addresses
        ]

    async def close(self) -> None:
        return None


def _split_target(value: str) -> tuple[str, int | None]:
    """``"127.0.0.1"`` or ``"127.0.0.1:19143"`` (an IPv4 address and an optional port)."""
    address, sep, port = value.partition(":")
    return (address, int(port)) if sep else (address, None)


def _override_for(override: dict[str, str], host: str) -> tuple[str, int | None] | None:
    """The seam's target for ``host``: an exact key, or a ``*.suffix`` key (any host under that suffix)."""
    key = host.lower().rstrip(".")
    if host in override:
        return _split_target(override[host])
    for pattern, value in override.items():
        if pattern.startswith("*.") and key.endswith(pattern[1:].lower()):
            return _split_target(value)
    return None


def validate_override(override: dict[str, str] | None) -> None:
    """The resolver seam may only point at loopback."""
    for host, value in (override or {}).items():
        if not ipaddress.ip_address(_split_target(value)[0]).is_loopback:
            raise ValueError(f"resolver override for {host!r} is not a loopback address")


def etag_matches(response_etag: str | None, expected: str | None) -> bool:
    """The response's ETag is the strong tag the verified read reported (weak tags never match)."""
    if not expected or not response_etag or response_etag.strip().startswith("W/"):
        return False
    tag = response_etag.strip()
    if len(tag) >= 2 and tag.startswith('"') and tag.endswith('"'):
        tag = tag[1:-1]
    return tag == expected


# ---- the download ---------------------------------------------------------------------------------

class Totals:
    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.downloaded = 0  # distinct raw bytes read so far
        self.written = 0  # bytes of the output so far


async def _read_body(resp: aiohttp.ClientResponse, path: str, ref: dict[str, Any], totals: Totals) -> tuple[str, int]:
    """Stream the body to ``path``; returns ``(subtype, size)``. Counts every byte as it arrives."""
    if resp.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
        raise fetch_failed("the image was sent compressed")
    declared = ref.get("size")
    size, head = 0, b""
    with open(path, "wb") as out:
        async for chunk in resp.content.iter_chunked(64 * 1024):
            size += len(chunk)
            totals.downloaded += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise fetch_failed("an image is larger than 20 MiB")
            if totals.downloaded > MAX_DISTINCT_BYTES:
                raise fetch_failed("the images together are larger than 36 MiB")
            if declared is not None and size > declared:
                raise fetch_failed("an image is larger than its stored size")
            if len(head) < 16:
                head += chunk[: 16 - len(head)]
            out.write(chunk)
    if declared is not None and size != declared:
        raise fetch_failed("an image does not have its stored size")
    subtype = sniff(head)
    if subtype is None:
        raise fetch_failed("a referenced document is not a PNG, JPEG, GIF or WebP image")
    return subtype, size


async def _fetch_one(session: aiohttp.ClientSession, task: dict[str, Any], ref: dict[str, Any], path: str, totals: Totals) -> tuple[str, int]:
    etag, vendor = ref.get("etag"), ref.get("vendor")
    if not etag or not vendor:
        raise fetch_failed("an image cannot be bound to the verified object")
    headers = {"Authorization": f"Bearer {task['token']}"}
    url = f"{task['storage_url']}/api/v1/document/internal/{ref['id']}/download"
    params = {"ifMatch": etag} if vendor == "local" else None
    async with session.get(url, params=params, headers=headers, allow_redirects=False) as resp:
        if resp.status == 412:
            raise fetch_failed("an image changed while the push ran; send the push again")
        if vendor == "local":
            if resp.status != 200:
                raise fetch_failed(f"an image could not be fetched (HTTP {resp.status})")
            got = await _read_body(resp, path, ref, totals)
            if not etag_matches(resp.headers.get("ETag"), etag):
                raise fetch_failed("an image is not the object that was verified")
            return got
        if resp.status != 200:
            raise fetch_failed(f"an image could not be fetched (HTTP {resp.status})")
        raw = await resp.content.read(SIGNED_URL_JSON_MAX + 1)
        if len(raw) > SIGNED_URL_JSON_MAX:
            raise fetch_failed("the signed URL answer is too large")
    try:
        signed = json.loads(raw)["signedUrl"]
        if not isinstance(signed, str):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise fetch_failed("the signed URL answer is not valid") from None
    admit = _check_pin(signed, ref, task)
    connector = aiohttp.TCPConnector(resolver=PinnedResolver(admit_non_public=admit, override=task.get("resolver_override")), ssl=_ssl_context(task))
    # The signed hop: a clean session (no Authorization, no environment proxies, no redirects, no decompression).
    async with aiohttp.ClientSession(connector=connector, auto_decompress=False, trust_env=False, timeout=_timeout(task)) as hop:
        async with hop.get(yarl.URL(signed, encoded=True), headers={"If-Match": f'"{etag}"'}, allow_redirects=False) as resp:
            if resp.status == 412:
                raise fetch_failed("an image changed while the push ran; send the push again")
            if resp.status != 200:
                raise fetch_failed(f"an image could not be fetched (signed URL answered HTTP {resp.status})")
            got = await _read_body(resp, path, ref, totals)
            if not etag_matches(resp.headers.get("ETag"), etag):
                raise fetch_failed("an image is not the object that was verified")
            return got


def _check_pin(signed: str, ref: dict[str, Any], task: dict[str, Any]) -> bool:
    try:
        listed = [tuple(o) for o in task.get("listed_origins", [])]
        return check_signed_url(signed, ref.get("object_url"), listed, allow_loopback=bool(task.get("allow_loopback")))  # type: ignore[arg-type]
    except Refused as e:
        # Logged once per refusal so an operator can see why an S3 reference failed (no URL, no token).
        sys.stderr.write(f"{LOG_PREFIX} refused ({e.message})\n")
        sys.stderr.flush()
        raise


def _ssl_context(task: dict[str, Any]) -> ssl.SSLContext | bool:
    ca_file = task.get("ca_file")
    return ssl.create_default_context(cafile=ca_file) if ca_file else True


def _timeout(task: dict[str, Any]) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=task.get("request_timeout_s", 60))


# ---- the splice -----------------------------------------------------------------------------------

def _splice(task: dict[str, Any], files: dict[str, tuple[str, int]], totals: Totals) -> dict[str, Any]:
    nonce = task["nonce"]
    token = re.compile(rb'"\\u0000pb-img:' + re.escape(nonce.encode()) + rb':(\d+)"')
    with open(task["sentinel_path"], "rb") as f:
        data = f.read()
    ids = [r["id"] for r in task["refs"]]
    digest = hashlib.sha256()
    cap = totals.cap

    def put(out: object, piece: bytes) -> None:
        totals.written += len(piece)
        if totals.written > cap:
            raise too_large(cap)
        digest.update(piece)
        out.write(piece)

    try:
        with open(task["out_path"], "wb") as out:
            position = 0
            for match in token.finditer(data):
                index = int(match.group(1))
                if index >= len(ids):
                    continue  # not a token of this push (cannot happen with a fresh nonce)
                put(out, data[position:match.start()])
                subtype, _size = files[ids[index]]
                put(out, b'"data:image/' + subtype.encode() + b";base64,")
                with open(os.path.join(task["work_dir"], ids[index] + ".img"), "rb") as image:
                    while chunk := image.read(CHUNK):
                        put(out, base64.b64encode(chunk))
                put(out, b'"')
                position = match.end()
            put(out, data[position:])
    except OSError as e:
        if e.errno == 27:  # EFBIG: RLIMIT_FSIZE, the file system's own bound
            raise too_large(cap) from None
        if e.errno in DISK_FULL:
            raise spool_full() from None
        raise
    return {"size": totals.written, "content_rev": digest.hexdigest()[:16]}


async def run_task(task: dict[str, Any]) -> dict[str, Any]:
    """Fetch every distinct image, then splice. Returns ``{size, content_rev}`` or raises ``Refused``."""
    totals = Totals(int(task["cap_bytes"]))
    files: dict[str, tuple[str, int]] = {}
    connector = aiohttp.TCPConnector(ssl=_ssl_context(task))
    async with aiohttp.ClientSession(connector=connector, auto_decompress=False, trust_env=False, timeout=_timeout(task)) as session:
        for ref in task["refs"]:
            path = os.path.join(task["work_dir"], ref["id"] + ".img")
            try:
                files[ref["id"]] = await _fetch_one(session, task, ref, path, totals)
            except (aiohttp.ClientError, TimeoutError, OSError) as e:
                if isinstance(e, OSError) and e.errno == 27:
                    raise too_large(totals.cap) from None
                if isinstance(e, OSError) and e.errno in DISK_FULL:
                    raise spool_full() from None
                # The class name and errno say what broke (refused, TLS, DNS, timeout); the text of the error can carry
                # the URL and its signature, so it is never copied.
                cause = type(e).__name__ + (f" errno {e.errno}" if isinstance(e, OSError) and e.errno else "")
                sys.stderr.write(f"{LOG_PREFIX} fetch failed ({cause})\n")
                sys.stderr.flush()
                raise fetch_failed(f"an image could not be fetched ({cause})") from None
    return await asyncio.to_thread(_splice, task, files, totals)


# ---- the process ----------------------------------------------------------------------------------

def _vm_size() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmSize:"):
                return int(line.split()[1]) * 1024
    return 0


def child_main(conn: object) -> None:
    """Entry of the spawned process: set the limits, then serve tasks until the pipe closes."""
    import resource
    import signal

    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)  # an oversize write then fails with EFBIG instead of killing the child
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_LIMIT, FSIZE_LIMIT))
    size = _vm_size()
    if size:
        limit = size + ADDRESS_SPACE_HEADROOM
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    conn.send(("ready", os.getpid()))
    while True:
        try:
            task = conn.recv()
        except EOFError:
            return
        if task is None:
            return
        try:
            conn.send(("ok", asyncio.run(run_task(task))))
        except Refused as r:
            conn.send(("refused", r.as_dict()))
        except MemoryError:
            conn.send(("refused", Refused(503, "PUSH_TIMEOUT", "The images could not be processed.", retryAfter=5).as_dict()))
        except Exception as e:
            conn.send(("error", f"{type(e).__name__}: {e}"[:300]))
