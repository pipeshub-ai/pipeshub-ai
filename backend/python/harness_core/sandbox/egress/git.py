"""The egress proxy's git rule (GIT-FLOW, GT-02/GT-03): one repository, fetch, and fast-forward pushes of one branch.

A destination with ``GitAccess`` is a git host the sandbox reaches only through the smart-HTTP protocol of its one
repository: ``info/refs`` and ``git-upload-pack`` always, ``git-receive-pack`` only when the access names a push
branch. Every other path on the host (its REST API, LFS, another repository, dumb HTTP) is refused.

A push is read before it is forwarded. Its ref updates must all name ``refs/heads/<push_branch>``, none may delete
it, and an update of an existing branch must be a fast-forward: the proxy finds the new commit in the pushed pack
and walks its parents (each commit's id is the hash of its content, so the walk cannot be forged) until it meets
the branch's old commit. A push it cannot prove is a fast-forward is refused, as is one it cannot read (a signed
push, SHA-256 objects, a thin pack that deltifies the new commit against an object the pack does not hold).
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from harness_core.archive_limits import (
    BoundedInflater,
    ExpansionLimits,
    ExpansionMeter,
    ExpansionRefused,
    inflate,
)
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused

if TYPE_CHECKING:
    from harness_core.sandbox.egress.types import ProxyRequest
    from harness_core.sandbox.types import GitAccess

UPLOAD_PACK = "git-upload-pack"
RECEIVE_PACK = "git-receive-pack"
ZERO_OID = "0" * 40
BRANCH_PREFIX = "refs/heads/"
# The most a git request body may inflate to: an encoded body is read whole, as a plain one is.
MAX_DECODED_BYTES = 64 * 1024 * 1024
# The most a pushed pack's objects may inflate to, all together: checking a push reads every object's stream.
MAX_PACK_INFLATED_BYTES = 1024**3
_OID = re.compile(r"[0-9a-f]{40}")
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,99}")
_MAX_REPOSITORY_SEGMENTS = 8
_BRANCH_CHARS = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_COMMIT, _TREE, _BLOB, _TAG, _OFS_DELTA, _REF_DELTA = 1, 2, 3, 4, 6, 7
_FLUSH = b"0000"
_CHUNK = 64 * 1024


@dataclass(frozen=True, slots=True)
class RefUpdate:
    old: str
    new: str
    ref: str


@dataclass(frozen=True, slots=True)
class ReceivePack:
    updates: tuple[RefUpdate, ...]
    pack: bytes


def repository_ok(repository: str) -> bool:
    """``owner/name`` (or a deeper group path): plain segments, no ``.git`` suffix, nothing to traverse."""
    parts = repository.split("/")
    return (
        0 < len(parts) <= _MAX_REPOSITORY_SEGMENTS
        and all(_SEGMENT.fullmatch(p) and p not in (".", "..") for p in parts)
        and not repository.endswith(".git")
    )


def branch_ok(branch: str) -> bool:
    """A branch name ``git check-ref-format --branch`` accepts, narrowed to plain characters."""
    if not _BRANCH_CHARS.fullmatch(branch) or branch.startswith(("-", "/", ".")) or branch.endswith(("/", ".", ".lock")):
        return False
    return ".." not in branch and "//" not in branch and "/." not in branch and branch != "HEAD"


def git_service(method: str, path: str, access: GitAccess) -> str:
    """The git service a request asks for, or refused: only the access's repository, only the smart protocol."""
    raw_path, _, query = path.partition("?")
    if "%" in raw_path or not raw_path.startswith("/"):
        raise _not_allowed("not a plain git path")
    prefix, operation = _split(raw_path[1:], access.repository)
    if operation == "info/refs" and method == "GET":
        service = query.removeprefix("service=") if query.startswith("service=") and "&" not in query else ""
    elif operation in (UPLOAD_PACK, RECEIVE_PACK) and method == "POST" and not query:
        service = operation
    else:
        raise _not_allowed("only the smart-HTTP git protocol of the repository is reachable")
    if not prefix or service not in (UPLOAD_PACK, RECEIVE_PACK):
        raise _not_allowed("only the smart-HTTP git protocol of the repository is reachable")
    if service == RECEIVE_PACK and access.push_branch is None:
        raise _not_allowed("this phase may not push")
    return service


def _split(path: str, repository: str) -> tuple[bool, str]:
    for stem in (f"{repository}.git/", f"{repository}/"):
        if path.startswith(stem):
            return True, path[len(stem) :]
    raise _not_allowed("another repository than the session's")


def _not_allowed(message: str) -> EgressRefused:
    return EgressRefused(R.GIT_OPERATION_NOT_ALLOWED, message)


def decoded_body(request: ProxyRequest) -> bytes:
    """The request body as git wrote it before encoding (git compresses large fetch negotiations with gzip)."""
    encoding = (request.header("content-encoding") or "").strip().lower()
    if encoding in ("", "identity"):
        return request.body
    if encoding not in ("gzip", "x-gzip"):
        raise EgressRefused(R.BODY_ENCODED, "encoded request bodies cannot be inspected")
    try:
        out, _ = inflate(request.body, ExpansionLimits.from_env(MAX_DECODED_BYTES), wbits=zlib.MAX_WBITS | 16)
    except zlib.error as exc:
        raise EgressRefused(R.MALFORMED_REQUEST, "the gzip body does not decode") from exc
    except ExpansionRefused as exc:
        raise EgressRefused(R.BODY_TOO_LARGE, "request body exceeds the proxy's limit") from exc
    return out


def inspectable(request: ProxyRequest) -> ProxyRequest:
    """The request as the proxy's content checks read it: a git body decoded, its encoding header gone."""
    if request.header("content-encoding") in (None, "", "identity"):
        return request
    headers = tuple((k, v) for k, v in request.headers if k.lower() != "content-encoding")
    return replace(request, headers=headers, body=decoded_body(request))


def check_git_request(request: ProxyRequest, path: str, access: GitAccess) -> None:
    """Refuse what ``access`` does not allow; a push is read and checked ref by ref (``request`` decoded)."""
    service = git_service(request.method.upper(), path, access)
    if service != RECEIVE_PACK or request.method.upper() != "POST":
        return
    assert access.push_branch is not None
    check_push(parse_receive_pack(request.body), access.push_branch)


def check_push(push: ReceivePack, branch: str) -> None:
    if not push.updates:
        raise EgressRefused(R.GIT_PUSH_UNREADABLE, "a push without ref updates")
    allowed = BRANCH_PREFIX + branch
    for update in push.updates:
        if update.ref != allowed:
            raise EgressRefused(R.GIT_REF_NOT_ALLOWED, "only the session branch may be pushed")
        if update.new == ZERO_OID:
            raise EgressRefused(R.GIT_DELETE_REFUSED, "the session branch may not be deleted")
    updates = [u for u in push.updates if u.old != ZERO_OID]
    if not updates:
        return
    commits = pack_commits(push.pack) if push.pack else {}
    for update in updates:
        if not fast_forward(commits, update.old, update.new):
            raise EgressRefused(R.GIT_FORCE_PUSH_REFUSED, "only a fast-forward of the session branch is pushed")


def fast_forward(commits: dict[str, tuple[str, ...]], old: str, new: str) -> bool:
    """Whether ``old`` is an ancestor of (or is) ``new``, by the parents of the pushed commits alone."""
    if old == new:
        return True
    seen: set[str] = set()
    frontier = [new]
    while frontier:
        oid = frontier.pop()
        if oid == old:
            return True
        if oid in seen:
            continue
        seen.add(oid)
        frontier.extend(commits.get(oid, ()))
    return False


def parse_receive_pack(body: bytes) -> ReceivePack:
    """The ref updates of a ``git-receive-pack`` request and the pack after them."""
    updates: list[RefUpdate] = []
    position = 0
    first = True
    while True:
        line, position = _pkt_line(body, position)
        if line is None:
            break
        text = line.rstrip(b"\n")
        if first:
            text, _, _capabilities = text.partition(b"\0")
            first = False
        decoded = text.decode("ascii", "strict") if text.isascii() else ""
        if decoded.startswith("shallow "):
            continue
        parts = decoded.split(" ")
        if len(parts) != 3 or not _OID.fullmatch(parts[0]) or not _OID.fullmatch(parts[1]):
            # A signed push (``push-cert``), SHA-256 ids, or anything else this rule cannot read.
            raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the push's ref updates are not readable")
        updates.append(RefUpdate(parts[0], parts[1], parts[2]))
    rest = body[position:]
    if rest and not rest.startswith(b"PACK"):
        # Push options (after a second flush) are not offered by this rule.
        raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the push carries something other than a pack")
    return ReceivePack(tuple(updates), rest)


def _pkt_line(body: bytes, position: int) -> tuple[bytes | None, int]:
    head = body[position : position + 4]
    if head == _FLUSH:
        return None, position + 4
    try:
        length = int(head, 16) if len(head) == 4 else -1
    except ValueError:
        length = -1
    if length < 5 or position + length > len(body):
        raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the push's ref updates are not readable")
    return body[position + 4 : position + length], position + length


def pack_commits(pack: bytes) -> dict[str, tuple[str, ...]]:
    """Every commit a pack holds, by id, with its parents; a pack that does not check out is refused."""
    if len(pack) < 32 or pack[:4] != b"PACK" or struct.unpack(">I", pack[4:8])[0] not in (2, 3):
        raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the pushed pack is not readable")
    if hashlib.sha1(pack[:-20], usedforsecurity=False).digest() != pack[-20:]:
        raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the pushed pack's checksum does not match")
    count = struct.unpack(">I", pack[8:12])[0]
    body = memoryview(pack)[: len(pack) - 20]
    position = 12
    # Offset -> (type, content) for commits only; offset -> base type for the rest (a delta's result has its base's).
    commits_at: dict[int, bytes] = {}
    kinds: dict[int, int] = {}
    commit_ids: dict[str, bytes] = {}
    # Every object of the pack is inflated, so the pack together is bounded like any archive (SEC-FS-03).
    meter = ExpansionMeter(ExpansionLimits.from_env(MAX_PACK_INFLATED_BYTES))
    try:
        for _ in range(count):
            meter.member()
            start = position
            kind, size, position = _object_header(body, position)
            if kind == _OFS_DELTA:
                distance, position = _offset(body, position)
                base = start - distance
                delta, position = _inflate(body, position, meter, size)
                kinds[start] = kinds.get(base, 0)
                if kinds[start] == _COMMIT:
                    commits_at[start] = _apply_delta(commits_at[base], delta)
            elif kind == _REF_DELTA:
                base_id = bytes(body[position : position + 20]).hex()
                delta, position = _inflate(body, position + 20, meter, size)
                base_commit = commit_ids.get(base_id)
                # A base outside the pack (a thin pack) has an unknown type: whatever it makes is not walked.
                kinds[start] = _COMMIT if base_commit is not None else 0
                if base_commit is not None:
                    commits_at[start] = _apply_delta(base_commit, delta)
            elif kind in (_COMMIT, _TREE, _BLOB, _TAG):
                content, position = _inflate(body, position, meter, size, keep=kind == _COMMIT)
                kinds[start] = kind
                if kind == _COMMIT:
                    commits_at[start] = content
            else:
                raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the pushed pack is not readable")
            if kinds[start] == _COMMIT:
                commit_ids[_commit_id(commits_at[start])] = commits_at[start]
    except ExpansionRefused as exc:
        raise EgressRefused(R.BODY_TOO_LARGE, f"the pushed pack is refused: {exc}") from exc
    except (zlib.error, IndexError, ValueError, KeyError) as exc:
        raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the pushed pack is not readable") from exc
    if position != len(body):
        raise EgressRefused(R.GIT_PUSH_UNREADABLE, "the pushed pack is not readable")
    return {oid: _parents(content) for oid, content in commit_ids.items()}


def _object_header(body: memoryview, position: int) -> tuple[int, int, int]:
    byte = body[position]
    kind = (byte >> 4) & 0x7
    size = byte & 0x0F
    shift = 4
    position += 1
    while byte & 0x80:
        byte = body[position]
        size |= (byte & 0x7F) << shift
        shift += 7
        position += 1
    return kind, size, position


def _offset(body: memoryview, position: int) -> tuple[int, int]:
    byte = body[position]
    value = byte & 0x7F
    position += 1
    while byte & 0x80:
        byte = body[position]
        value = ((value + 1) << 7) | (byte & 0x7F)
        position += 1
    return value, position


def _inflate(
    body: memoryview, position: int, meter: ExpansionMeter, size: int, *, keep: bool = True
) -> tuple[bytes, int]:
    """One zlib stream from ``position``: its content (empty unless ``keep``) and where the next object starts.

    Counted against the pack's ``meter`` as it inflates, and never past the ``size`` its header declares: a bomb
    is refused at the limit, not after it."""
    inflater = BoundedInflater(meter, most=size)
    out: list[bytes] = []
    fed = position
    while not inflater.eof:
        if fed >= len(body):
            raise ValueError("truncated object")
        chunk = body[fed : fed + _CHUNK]
        fed += len(chunk)
        data = inflater.feed(chunk)
        if keep:
            out.append(data)
    if inflater.produced != size:
        raise ValueError("object size")
    return b"".join(out), fed - len(inflater.unused_data)


def _apply_delta(base: bytes, delta: bytes) -> bytes:
    source_size, position = _varint(delta, 0)
    target_size, position = _varint(delta, position)
    if source_size != len(base):
        raise ValueError("delta base size")
    out = bytearray()
    while position < len(delta):
        op = delta[position]
        position += 1
        if op & 0x80:
            offset = size = 0
            for bit in range(4):
                if op & (1 << bit):
                    offset |= delta[position] << (8 * bit)
                    position += 1
            for bit in range(3):
                if op & (1 << (4 + bit)):
                    size |= delta[position] << (8 * bit)
                    position += 1
            size = size or 0x10000
            if offset + size > len(base):
                raise ValueError("delta copy out of range")
            out += base[offset : offset + size]
        elif op:
            out += delta[position : position + op]
            position += op
        else:
            raise ValueError("delta opcode 0")
    if len(out) != target_size:
        raise ValueError("delta target size")
    return bytes(out)


def _varint(data: bytes, position: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, position


def _commit_id(content: bytes) -> str:
    return hashlib.sha1(b"commit %d\0" % len(content) + content, usedforsecurity=False).hexdigest()


def _parents(content: bytes) -> tuple[str, ...]:
    parents: list[str] = []
    for line in content.split(b"\n"):
        if not line:
            break
        if line.startswith(b"parent "):
            parents.append(line[7:].decode("ascii"))
    return tuple(parents)


__all__ = [
    "RECEIVE_PACK",
    "UPLOAD_PACK",
    "ZERO_OID",
    "ReceivePack",
    "RefUpdate",
    "branch_ok",
    "check_git_request",
    "check_push",
    "decoded_body",
    "fast_forward",
    "git_service",
    "inspectable",
    "pack_commits",
    "parse_receive_pack",
    "repository_ok",
]
