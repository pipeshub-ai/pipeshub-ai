"""Server-side mirror of a repository a client pushes (standard library only).

Layout: ``<base>/<org>/<user>/<sha256(repoKey)[:32]>/{generations/gNNNNNN/, current, readers/, staging/, repo.json, files.json}``. A generation is
a complete, immutable tree of the committed files; ``current`` names the one the Local Repository connector walks. An upload (and a deletion) is
staged, and ``commit()`` builds the next generation beside the current one and switches ``current`` to it with one atomic rename, so a sync
(which reads one generation from start to end) sees the old tree or the new one and never a mix, and a commit that fails leaves the old one; the two JSON files (repo identity and generation; path -> sha256/size) live beside it
so the walk never sees them. Org and user come from the caller's token, never from the request; the client names a repository
only by an opaque key, so it cannot choose a path on the server.
"""
from __future__ import annotations

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
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.connectors.sources.local_folder import scan
from app.connectors.sources.local_repo import code_rule

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


def _same_bytes(a: Path, b: Path) -> bool:
    """Whether two files hold the same bytes (size first, then a hash read in chunks)."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        digests = []
        for path in (a, b):
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            digests.append(h.digest())
        return digests[0] == digests[1]
    except OSError:
        return False  # cannot tell: treat as different, so the graph is rebuilt rather than left stale


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


def find_by_connector(org_id: str, connector_id: str, base: Path | None = None) -> list[Repo]:
    """The mirrors in ``org_id`` that belong to ``connector_id``, found by the connector id each repo.json records.

    For a connector whose owner or repository key cannot be read from its own record: the org is a path segment, so no other org's files are
    looked at. A repo.json that cannot be read is skipped with a log line (it cannot be said to belong to anyone)."""
    org_dir = (base or store_base()) / _segment(org_id)
    found: list[Repo] = []
    for meta_file in org_dir.glob("*/*/repo.json") if org_dir.is_dir() else ():
        try:
            meta = read_json_object(meta_file)
        except StoreError:
            continue
        if meta.get("connectorId") == connector_id and isinstance(meta.get("repoKey"), str):
            found.append(Repo(org_id, meta_file.parent.parent.name, meta["repoKey"], base))
    return found


GENERATION_RE = re.compile(r"g\d{6,12}")
# A sync marks the generation it reads; a mark older than this is a sync that died, and no longer keeps its generation.
# shortcut: a sync that really runs longer than this loses its generation to the next commit; upgrade by refreshing the mark while it runs.
READER_TTL_SECONDS = 6 * 3600


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
        self.generations = self.dir / "generations"
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

    # ------------------------------------------------------------ generations
    def current_tree(self) -> Path | None:
        """The directory of the current generation, resolved to a real folder under this repository's ``generations/``; None before the first.

        The pointer is read as a name and checked against the generation pattern and against the resolved location, so neither a damaged
        pointer nor a link planted in place of a generation can lead anywhere else."""
        try:
            name = (self.dir / "current").read_text("utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreError("state_unreadable") from exc
        if not GENERATION_RE.fullmatch(name):
            raise StoreError("state_unreadable")
        path = self.generations / name
        real = path.resolve()
        if path.is_symlink() or not real.is_dir() or real.parent != self.generations.resolve():
            raise StoreError("state_unreadable")
        return real

    @property
    def tree(self) -> Path:
        """The current generation (a path that does not exist before the first commit)."""
        return self.current_tree() or self.generations / "none"

    def _ensure_generation(self) -> None:
        """A new repository starts with an empty generation, so its first sync has a root to walk."""
        if (self.dir / "current").exists():
            return
        self.generations.mkdir(parents=True, exist_ok=True)
        first = self.generations / "g000000"
        first.mkdir(exist_ok=True)
        self._point_at(first.name)

    def _point_at(self, name: str) -> None:
        """Switch ``current`` with one atomic rename: a reader sees the old name or the new one."""
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".current.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(name)
            os.replace(tmp, self.dir / "current")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def pin(self) -> tuple[Path, Any]:
        """The current generation and a ``release`` function: while the mark is held, a commit does not remove that generation.

        The connector pins one generation at the start of a sync and reads only that one until it is done. The pointer is read and the mark
        made under the repository lock, so a commit cannot remove the generation between the two."""
        with self._lock:
            tree = self.current_tree()
            if tree is None:
                raise StoreError("not_pushed")
            readers = self.dir / "readers"
            readers.mkdir(exist_ok=True)
            mark = readers / f"{tree.name}.{uuid.uuid4().hex}"
            mark.touch()
        return tree, lambda: mark.unlink(missing_ok=True)

    def _read_generations(self) -> set[str]:
        """Names of the generations a live sync is reading; marks of syncs that died are cleaned away."""
        live: set[str] = set()
        readers = self.dir / "readers"
        for mark in readers.glob("*.*") if readers.is_dir() else ():
            try:
                if time.time() - mark.stat().st_mtime > READER_TTL_SECONDS:
                    mark.unlink(missing_ok=True)
                else:
                    live.add(mark.name.split(".", 1)[0])
            except OSError:
                live.add(mark.name.split(".", 1)[0])  # cannot tell: keep it
        return live

    def _retire_old_generations(self, keep: str) -> None:
        """Remove every generation but ``keep`` that no sync is reading. Best effort (logged): the commit has already succeeded."""
        in_use = self._read_generations() | {keep}
        for old in self.generations.iterdir():
            if old.name in in_use or old.is_symlink():
                continue
            try:
                shutil.rmtree(old)
            except OSError as exc:
                logger.warning("local repo: could not remove the old generation %s: %s", old, exc)

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
            self._ensure_generation()
            meta = {**{"repoKey": self.key, "generation": 0, "committedAt": 0}, **self.meta(), **updates}
            self._write_json("repo.json", meta)
            return meta

    def commit(self) -> dict[str, Any]:
        """Publish what is staged as one new generation: the tree the sync walks becomes exactly the files the manifest lists.

        The next generation is built beside the current one (a hard link of each unchanged file, a hard link of each staged one, so it costs no
        bytes the quota does not already count) and ``current`` is switched to it with one atomic rename. A sync reads one generation from start
        to end: it sees the old tree or the new one, never a mix; and a commit that fails before the switch leaves the old generation serving and
        the staged files in place, so it can simply be repeated.

        Whether the commit changes code is decided by comparing what it publishes with the generation it replaces (a code path added, removed or
        with other bytes): staging and dropping a file, or sending the bytes already published, changes nothing. A commit that changes code
        records ``pendingCodeChange`` (the name of the generation it is about to switch to) before the switch, and the metadata write that follows
        the switch clears it, so a repeat after a failure between the two (when the old generation is no longer there to compare with) still
        asks for the graph build. A leftover that names a generation which never became current is ignored.
        """
        with self._lock:
            meta = self.meta()
            manifest = self.manifest()
            self._ensure_generation()
            current = self.current_tree()
            carried = current is not None and meta.get("pendingCodeChange") == current.name  # a commit that switched but did not finish
            changed = self._changes_code(manifest, current)
            new_name = self._build_generation(manifest)
            if changed and meta.get("pendingCodeChange") != new_name:
                self.save_meta(pendingCodeChange=new_name)
            self._point_at(new_name)
            updates: dict[str, Any] = {"generation": int(meta.get("generation", 0)) + 1, "committedAt": int(time.time() * 1000)}
            if changed or carried:
                updates["codeChangedAt"] = updates["committedAt"]  # the code graph owes a build after this commit
            updates["pendingCodeChange"] = False
            saved = self.save_meta(**updates)
            self._clear_staging()
            self._retire_old_generations(new_name)
            return saved

    def _changes_code(self, manifest: dict[str, list[Any]], current: Path | None) -> bool:
        """Whether publishing ``manifest`` changes the code records of ``current``.

        Only files that become code records count (``code_rule.is_code_record``, the rule the sync itself applies): a change to a file the sync
        leaves out (``dist/``, ``third_party/``, a minified or generated file) moves nothing the code graph holds. A file that becomes a code
        record, stops being one, is added or removed, or has other bytes, is a change."""
        def code(path: Path, rel: str) -> bool:
            return code_rule.is_code_record(rel, lambda: code_rule.read_head(path))

        for path in manifest:
            staged = self.staging / path
            if not staged.is_file() or staged.is_symlink():
                continue  # not staged: it carries over from the current generation as it is
            old = (current / path) if current else None
            had = old is not None and old.is_file() and not old.is_symlink() and code(old, path)
            now = code(staged, path)
            if had != now or (now and not _same_bytes(staged, old)):
                return True
        for dirpath, _dirs, files in os.walk(current, followlinks=False) if current else ():
            rel_dir = os.path.relpath(dirpath, current).replace(os.sep, "/")
            for name in files:
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                if rel not in manifest and code(Path(dirpath, name), rel):
                    return True
        return False

    def _build_generation(self, manifest: dict[str, list[Any]]) -> str:
        """Build ``generations/gNNNNNN`` from the manifest: the staged file where there is one, else the current generation's. Returns its name."""
        self.generations.mkdir(parents=True, exist_ok=True)
        for stale in self.generations.glob(".build-*"):  # left by a commit that died
            shutil.rmtree(stale, ignore_errors=True)
        numbers = [int(p.name[1:]) for p in self.generations.iterdir() if GENERATION_RE.fullmatch(p.name)]
        number = max(numbers, default=-1) + 1
        build = self.generations / f".build-{number:06d}"
        build.mkdir()
        current = self.current_tree()
        try:
            for path in manifest:
                staged, committed = self.staging / path, (current / path) if current else None
                if staged.is_file() and not staged.is_symlink():
                    src = staged
                elif committed is not None and committed.is_file() and not committed.is_symlink():
                    src = committed
                else:
                    continue  # the manifest lists it but no content is held (the diff asks for it again)
                dest = build / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.parent.resolve().is_relative_to(build.resolve()):
                    raise StoreError("invalid_path")
                try:
                    os.link(src, dest)
                except OSError:  # no hard links here: a copy costs time and transient space, not quota
                    shutil.copy2(src, dest)
            name = f"g{number:06d}"
            os.rename(build, self.generations / name)
        except BaseException:
            shutil.rmtree(build, ignore_errors=True)
            raise
        return name

    def _clear_staging(self) -> None:
        """Remove the staged files once they are published (they are linked into the new generation); best effort, nothing depends on it."""
        shutil.rmtree(self.staging, ignore_errors=True)

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
