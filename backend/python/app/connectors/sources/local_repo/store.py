"""Server-side mirror of a repository a client pushes (standard library only).

Layout: ``<base>/<org>/<user>/<sha256(repoKey)[:32]>/{tree/, staging/, repo.json, files.json}``. ``tree/`` holds the committed files and is
what the Local Repository connector walks; an upload (and a deletion) is staged and reaches ``tree/`` only when ``commit()`` runs, so the sync
never sees a half-sent push and a committed file keeps its content until the next commit; the two JSON files (repo identity and generation; path -> sha256/size) live beside it
so the walk never sees them. Org and user come from the caller's token, never from the request; the client names a repository
only by an opaque key, so it cannot choose a path on the server.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.connectors.sources.local_folder import scan
from app.modules.parsers.code_parser.lang_config import detect_language

logger = logging.getLogger(__name__)

LOCAL_REPO_CONNECTOR_NAME = "Local Repository"
REPO_KEY = "repo_key"
BASE_ENV = "PIPESHUB_LOCAL_REPO_STORE"
KEY_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")  # matched whole: `$` would also accept a trailing newline
SEGMENT_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")  # org and user ids from the token; still never trusted as a path
SHA_RE = re.compile(r"[0-9a-f]{64}")
MAX_PATH_BYTES = 1024

DEFAULT_LIMITS = {
    "maxFiles": 20000,
    "maxFileBytes": 1024 * 1024,
    "maxBatchFiles": 100,
    "maxBatchBytes": 4 * 1024 * 1024,
    "maxManifestEntries": 20000,
    "maxReposPerUser": 20,
    "maxUserBytes": 5 * 1024**3,  # all of one user's repositories together
    "maxTotalBytes": 512 * 1024 * 1024,  # what one repository may hold in all, so a user cannot fill the disk with 20000 files of the largest size
}

_DEFAULT_IGNORE = scan.IgnoreStack().extended("", scan.parse_ignore(scan.PUSHED_REPO_IGNORES))
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


class StoreError(ValueError):
    """A request the store refuses; ``reason`` is the stable code returned to the client."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def read_json_object(path: Path) -> dict[str, Any]:
    """The JSON object in ``path``; ``{}`` only when the file does not exist. A file that cannot be read or decoded is ``StoreError('state_unreadable')``:
    an unreadable manifest is not an empty repository (an upload would replace it, a commit would sweep the files, a sync would retire the records)."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.error("local repo: cannot read %s: %s", path, exc)
        raise StoreError("state_unreadable") from exc
    if not isinstance(data, dict):
        logger.error("local repo: %s does not hold an object", path)
        raise StoreError("state_unreadable")
    return data


def limits() -> dict[str, int]:
    out = dict(DEFAULT_LIMITS)
    for name, env in (("maxFiles", "PIPESHUB_LOCAL_REPO_MAX_FILES"), ("maxFileBytes", "PIPESHUB_LOCAL_REPO_MAX_FILE_BYTES"),
                      ("maxTotalBytes", "PIPESHUB_LOCAL_REPO_MAX_TOTAL_BYTES"), ("maxReposPerUser", "PIPESHUB_LOCAL_REPO_MAX_REPOS_PER_USER"),
                      ("maxUserBytes", "PIPESHUB_LOCAL_REPO_MAX_USER_BYTES")):
        try:  # noqa: SIM105
            out[name] = max(1, int(os.environ.get(env, out[name])))
        except ValueError:
            pass
    return out


def store_base() -> Path:
    raw = os.environ.get(BASE_ENV)
    return Path(raw) if raw else Path(os.path.expanduser("~")) / "pipeshub-local-repos"


def clean_path(raw: Any) -> str:  # noqa: ANN401
    """The NFC form of a repo-relative path, or ``StoreError('invalid_path')``."""
    if not isinstance(raw, str) or not raw:
        raise StoreError("invalid_path")
    path = unicodedata.normalize("NFC", raw)
    if len(path.encode("utf-8", "surrogatepass")) > MAX_PATH_BYTES or "\\" in path or path.startswith("/") or path.endswith("/"):
        raise StoreError("invalid_path")
    for seg in path.split("/"):
        # 255 bytes is a file name's limit on the common file systems; longer would end in an OSError, a 500 for the whole batch.
        # '<' and '>' are markup: names are listed in lists and search results, and this API skips the sanitiser.
        # Format characters (category Cf: bidi overrides and isolates, zero-width marks) make a name read as another one in a list.
        if seg in ("", ".", "..", ".git") or any(ord(c) < 32 or ord(c) == 127 or c in "<>" or unicodedata.category(c) == "Cf" for c in seg) or len(seg.encode("utf-8", "surrogatepass")) > 255:
            raise StoreError("invalid_path")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        raise StoreError("invalid_path") from None
    return path


def is_ignored(path: str) -> bool:
    """The server's default ignore list (the same one the walk applies), for the file and each directory above it."""
    parts = path.split("/")
    return any(_DEFAULT_IGNORE.ignored("/".join(parts[:i]), True) for i in range(1, len(parts))) or _DEFAULT_IGNORE.ignored(path, False)


def _lock_for(key: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _segment(value: str) -> str:
    if not isinstance(value, str) or not SEGMENT_RE.fullmatch(value) or value in (".", ".."):
        raise StoreError("invalid_identity")
    return value


def repo_dir(org_id: str, user_id: str, repo_key: str, base: Path | None = None) -> Path:
    if not KEY_RE.fullmatch(repo_key or ""):
        raise StoreError("invalid_key")
    return (base or store_base()) / _segment(org_id) / _segment(user_id) / hashlib.sha256(repo_key.encode()).hexdigest()[:32]


def tree_of(org_id: str, user_id: str, repo_key: str, base: Path | None = None) -> Path:
    return repo_dir(org_id, user_id, repo_key, base) / "tree"


@dataclass
class Diff:
    upload: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    rejected: list[dict[str, str]] = field(default_factory=list)


class Repo:
    """One mirrored repository. Methods are blocking (file I/O); call them from a thread."""

    def __init__(self, org_id: str, user_id: str, key: str, base: Path | None = None) -> None:
        self.key, self.dir = key, repo_dir(org_id, user_id, key, base)
        self.tree = self.dir / "tree"
        self.staging = self.dir / "staging"
        self._lock = _lock_for(str(self.dir))
        # One lock per (org, user), taken BEFORE a repository's own lock wherever both are needed (so no pair of threads can wait on each other):
        # the per-user quota is read and then spent across several of the user's repositories.
        self._user_lock = _lock_for(f"user:{self.dir.parent}")
        self.limits = limits()

    # ------------------------------------------------------------ state
    def exists(self) -> bool:
        return (self.dir / "repo.json").is_file()

    def meta(self) -> dict[str, Any]:
        return read_json_object(self.dir / "repo.json")

    def manifest(self) -> dict[str, list[Any]]:
        return read_json_object(self.dir / "files.json")

    def _write_json(self, name: str, data: Any) -> None:  # noqa: ANN401
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.dir / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def save_meta(self, **updates: Any) -> dict[str, Any]:  # noqa: ANN401
        with self._lock:
            self.tree.mkdir(parents=True, exist_ok=True)
            meta = {**{"repoKey": self.key, "generation": 0, "committedAt": 0}, **self.meta(), **updates}
            self._write_json("repo.json", meta)
            return meta

    def commit(self) -> dict[str, Any]:
        """Publish what is staged: the tree becomes exactly the files the manifest lists.

        Files the manifest no longer lists (deleted, or left by an upload that stopped half way) are removed from the tree, and each staged file
        replaces its committed version in one rename. Nothing of this is visible to the sync before this call, and a call that is interrupted
        can simply be repeated. The sync therefore indexes only committed files, and a committed file keeps its content (and its indexed record)
        until the next commit.
        """
        with self._lock:
            meta = self.meta()
            manifest = self.manifest()
            self.tree.mkdir(parents=True, exist_ok=True)
            code_changed = self._sweep(manifest)
            code_changed = self._promote(manifest) or code_changed
            updates: dict[str, Any] = {"generation": int(meta.get("generation", 0)) + 1, "committedAt": int(time.time() * 1000)}
            if code_changed:
                updates["codeChangedAt"] = updates["committedAt"]  # the code graph owes a build after this commit
            return self.save_meta(**updates)

    def _promote(self, manifest: dict[str, list[Any]]) -> bool:
        """Move every staged file into the tree; True when one of them is code."""
        code = False
        tree = self.tree.resolve()
        for path in manifest:
            src, dest = self.staging / path, self.tree / path
            if not src.is_file():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.parent.resolve().is_relative_to(tree):
                raise StoreError("invalid_path")
            os.replace(src, dest)
            code = code or bool(detect_language(path))
        for dirpath, _dirs, _files in os.walk(self.staging, topdown=False):
            if Path(dirpath) != self.staging:
                with contextlib.suppress(OSError):
                    Path(dirpath).rmdir()
        return code

    def _sweep(self, manifest: dict[str, list[Any]]) -> bool:
        """Remove every file (and any link, which is never written here) in the tree that the manifest does not list, then the emptied folders.

        True when one of the removed files is code."""
        code = False
        for dirpath, dirnames, filenames in os.walk(self.tree, topdown=False, followlinks=False):
            rel_dir = os.path.relpath(dirpath, self.tree).replace(os.sep, "/")
            for name in filenames:
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                if rel not in manifest:
                    Path(dirpath, name).unlink(missing_ok=True)
                    code = code or bool(detect_language(rel))
            for name in dirnames:
                if Path(dirpath, name).is_symlink():
                    Path(dirpath, name).unlink(missing_ok=True)
            if rel_dir != ".":
                with contextlib.suppress(OSError):  # a folder that still holds files stays
                    Path(dirpath).rmdir()
        return code

    # ------------------------------------------------------------ what this user holds
    def _user_dir(self) -> Path:
        return self.dir.parent

    def user_repos(self) -> int:
        return sum(1 for d in self._user_dir().glob("*/repo.json")) if self._user_dir().is_dir() else 0

    def user_bytes(self) -> int:
        """What the user's repositories hold in all (the sizes in each manifest), this one included."""
        total = 0
        for f in self._user_dir().glob("*/files.json") if self._user_dir().is_dir() else ():
            try:
                total += sum(int(v[1]) for v in read_json_object(f).values())
            except (TypeError, ValueError, IndexError) as exc:
                raise StoreError("state_unreadable") from exc
        return total

    # ------------------------------------------------------------ changes
    def _remove(self, manifest: dict[str, list[Any]], path: str) -> None:
        """Drop ``path`` from the manifest and its staged file; the committed copy in the tree goes at the next commit."""
        manifest.pop(path, None)
        target = self.staging / path
        try:
            target.unlink()
        except FileNotFoundError:
            return
        except IsADirectoryError:
            return
        parent = target.parent
        while parent != self.staging:  # drop directories this removal emptied
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def diff(self, files: list[dict[str, Any]], complete: bool, deleted: list[str]) -> Diff:  # noqa: FBT001
        out, lim = Diff(), self.limits
        with self._lock:
            manifest = self.manifest()
            before = set(manifest)
            for raw in deleted:
                try:
                    path = clean_path(raw)
                except StoreError as exc:
                    out.rejected.append({"path": str(raw)[:200], "reason": exc.reason})
                    continue
                if path in manifest:
                    self._remove(manifest, path)
                    out.deleted.append(path)
            listed: set[str] = set()
            room = lim["maxFiles"] - len(manifest)
            for entry in files:
                try:
                    path = clean_path(entry.get("path"))
                except StoreError as exc:
                    out.rejected.append({"path": str(entry.get("path"))[:200], "reason": exc.reason})
                    continue
                sha, size = entry.get("sha256"), entry.get("size")
                if not isinstance(sha, str) or not SHA_RE.fullmatch(sha) or not isinstance(size, int) or size < 0:
                    out.rejected.append({"path": path, "reason": "invalid_path"})
                    continue
                if is_ignored(path):
                    out.rejected.append({"path": path, "reason": "ignored"})
                    continue
                if size > lim["maxFileBytes"]:
                    out.rejected.append({"path": path, "reason": "too_large"})
                    continue
                listed.add(path)
                cur = manifest.get(path)
                if cur and cur[0] == sha and ((self.staging / path).is_file() or (self.tree / path).is_file()):
                    out.unchanged += 1
                    continue
                if not cur:
                    if room <= 0:
                        out.rejected.append({"path": path, "reason": "too_many_files"})
                        continue
                    room -= 1
                out.upload.append(path)
            if complete:
                for path in sorted(before - listed - set(out.deleted)):
                    if path in manifest:
                        self._remove(manifest, path)
                        out.deleted.append(path)
            if out.deleted:
                self._write_json("files.json", manifest)
        return out

    def store(self, files: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, str]]]:
        stored: list[str] = []
        rejected: list[dict[str, str]] = []
        lim = self.limits
        with self._user_lock, self._lock:
            manifest = self.manifest()
            self.staging.mkdir(parents=True, exist_ok=True)
            tree = self.staging.resolve()
            total = sum(int(v[1]) for v in manifest.values())
            user_total = self.user_bytes()
            for entry in files:
                try:
                    path = clean_path(entry.get("path"))
                except StoreError as exc:
                    rejected.append({"path": str(entry.get("path"))[:200], "reason": exc.reason})
                    continue
                content = entry.get("content")
                if is_ignored(path):
                    reason = "ignored"
                elif not isinstance(content, str) or "\x00" in content:
                    reason = "binary"
                else:
                    try:
                        data = content.encode("utf-8")
                    except UnicodeEncodeError:
                        data = b""
                        reason = "binary"
                    else:
                        reason = ("too_large" if len(data) > lim["maxFileBytes"]
                                  else "hash_mismatch" if hashlib.sha256(data).hexdigest() != entry.get("sha256")
                                  else "too_many_files" if path not in manifest and len(manifest) >= lim["maxFiles"]
                                  else "too_large" if total - (manifest.get(path) or [0, 0])[1] + len(data) > lim["maxTotalBytes"]
                                  else "user_quota" if user_total - (manifest.get(path) or [0, 0])[1] + len(data) > lim["maxUserBytes"] else "")
                if reason:
                    rejected.append({"path": path, "reason": reason})
                    continue
                try:
                    self._write_file(tree, manifest, path, data)
                except StoreError as exc:
                    rejected.append({"path": path, "reason": exc.reason})
                    continue
                total += len(data) - (manifest.get(path) or [0, 0])[1]
                user_total += len(data) - (manifest.get(path) or [0, 0])[1]
                manifest[path] = [hashlib.sha256(data).hexdigest(), len(data)]
                stored.append(path)
            self.tree.mkdir(parents=True, exist_ok=True)
            self._write_json("files.json", manifest)
        return stored, rejected

    def _write_file(self, staging: Path, manifest: dict[str, list[Any]], path: str, data: bytes) -> None:
        dest = staging / path
        parts = path.split("/")
        for i in range(1, len(parts)):  # a file where a directory is now needed goes away
            anc = "/".join(parts[:i])
            if anc in manifest:
                self._remove(manifest, anc)
        if dest.is_dir():
            raise StoreError("invalid_path")  # a directory with files in it; the client sends those deletions first
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.parent.resolve().is_relative_to(staging):
            raise StoreError("invalid_path")
        tmpdir = self.dir / "tmp"  # outside tree/, so the walk never sees a half-written file
        tmpdir.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=tmpdir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def destroy(self, only_connector_id: str | None = None) -> bool:
        """Remove the mirror; False when it was left alone. A removal that fails is logged and raised as ``StoreError('remove_failed')``, never silent.

        ``only_connector_id``: remove it only while it still belongs to that connector. A delete that runs later (the connector's deletion is an
        event) must not take the files of a repository that was registered again under the same key in the meantime."""
        with self._lock:
            if only_connector_id is not None and self.meta().get("connectorId") != only_connector_id:
                logger.info("local repo: %s now belongs to another connector; its files are left", self.dir)
                return False
            try:
                shutil.rmtree(self.dir)
            except FileNotFoundError:
                return True
            except OSError as exc:
                logger.error("local repo: could not remove %s: %s", self.dir, exc)
                raise StoreError("remove_failed") from exc
            return True
