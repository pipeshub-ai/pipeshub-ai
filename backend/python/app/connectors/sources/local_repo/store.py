"""Server-side mirror of a repository a client pushes (standard library only).

Layout: ``<base>/<org>/<user>/<sha256(repoKey)[:32]>/{tree/, repo.json, files.json}``. ``tree/`` holds the pushed files and is
what the Local Repository connector walks; the two JSON files (repo identity and generation; path -> sha256/size) live beside it
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
        self._lock = _lock_for(str(self.dir))
        self.limits = limits()

    # ------------------------------------------------------------ state
    def exists(self) -> bool:
        return (self.dir / "repo.json").is_file()

    def meta(self) -> dict[str, Any]:
        try:
            return json.loads((self.dir / "repo.json").read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def manifest(self) -> dict[str, list[Any]]:
        try:
            data = json.loads((self.dir / "files.json").read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

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
        """Publish what is stored: the files the sync may index are the ones listed here, so an upload that never got its commit is not searchable.

        When the last manifest was complete (the whole repository), files in the tree that it does not list (left by an upload that stopped
        half way) are removed first.
        shortcut: a path that is committed and then overwritten by an upload that is not yet committed is indexed with the new content at
        the next sync, because the file is replaced whole (a write is atomic); upgrade by committing the hash and skipping files that differ.
        """
        with self._lock:
            meta = self.meta()
            manifest = self.manifest()
            if meta.get("completeManifest"):
                self._sweep(manifest)
            self.tree.mkdir(parents=True, exist_ok=True)
            self._write_json("committed.json", {path: row[0] for path, row in manifest.items()})
            return self.save_meta(generation=int(meta.get("generation", 0)) + 1, committedAt=int(time.time() * 1000))

    def committed(self) -> dict[str, str]:
        """path -> sha256 of the files published by the last commit (none before the first)."""
        try:
            data = json.loads((self.dir / "committed.json").read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _sweep(self, manifest: dict[str, list[Any]]) -> None:
        """Remove every file (and any link, which is never written here) in the tree that the manifest does not list, then the emptied folders."""
        for dirpath, dirnames, filenames in os.walk(self.tree, topdown=False, followlinks=False):
            rel_dir = os.path.relpath(dirpath, self.tree).replace(os.sep, "/")
            for name in filenames:
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                if rel not in manifest:
                    Path(dirpath, name).unlink(missing_ok=True)
            for name in dirnames:
                if Path(dirpath, name).is_symlink():
                    Path(dirpath, name).unlink(missing_ok=True)
            if rel_dir != ".":
                with contextlib.suppress(OSError):  # a folder that still holds files stays
                    Path(dirpath).rmdir()

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
                total += sum(int(v[1]) for v in json.loads(f.read_text("utf-8")).values())
            except (OSError, ValueError, TypeError, IndexError):
                continue
        return total

    # ------------------------------------------------------------ changes
    def _remove(self, manifest: dict[str, list[Any]], path: str) -> None:
        manifest.pop(path, None)
        target = self.tree / path
        try:
            target.unlink()
        except FileNotFoundError:
            return
        except IsADirectoryError:
            return
        parent = target.parent
        while parent != self.tree:  # drop directories this removal emptied
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
                if cur and cur[0] == sha and (self.tree / path).is_file():
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
            if bool(self.meta().get("completeManifest")) != complete:
                self.save_meta(completeManifest=complete)
        return out

    def store(self, files: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, str]]]:
        stored: list[str] = []
        rejected: list[dict[str, str]] = []
        lim = self.limits
        with self._lock:
            manifest = self.manifest()
            tree = self.tree.resolve()
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
            if any(detect_language(p) for p in stored):  # the code graph owes a build after these
                self.save_meta(codeChangedAt=int(time.time() * 1000))
        return stored, rejected

    def _write_file(self, tree: Path, manifest: dict[str, list[Any]], path: str, data: bytes) -> None:
        dest = tree / path
        parts = path.split("/")
        for i in range(1, len(parts)):  # a file where a directory is now needed goes away
            anc = "/".join(parts[:i])
            if anc in manifest:
                self._remove(manifest, anc)
        if dest.is_dir():
            raise StoreError("invalid_path")  # a directory with files in it; the client sends those deletions first
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.parent.resolve().is_relative_to(tree):
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

    def destroy(self) -> None:
        """Remove the mirror. A removal that fails is logged and raised as ``StoreError('remove_failed')``, never silent."""
        with self._lock:
            try:
                shutil.rmtree(self.dir)
            except FileNotFoundError:
                return
            except OSError as exc:
                logger.error("local repo: could not remove %s: %s", self.dir, exc)
                raise StoreError("remove_failed") from exc
