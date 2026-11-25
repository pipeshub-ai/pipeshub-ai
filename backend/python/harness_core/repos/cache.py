"""The deployment's repository cache (WP-63 part A): one bounded folder on the worker's disk, shared by its runs.

Each entry is one fetch, keyed by org, URL, ref and depth (an org never reads another's), and holds what the sandbox
is handed: a tar of the bare repository, beside a small ``meta.json``. An entry is reused while it is younger than
``max_age_s``, so a second clone of the same repository in a short while moves nothing; after that it is fetched again.
The total is held under ``max_bytes`` by evicting the least recently used entries, never one a hand-off is reading
(``pinned``). Entries are written under a temporary name and renamed into place, so a crash leaves no half entry: a
leftover temporary folder is removed at start.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.logs import HarnessLog
from harness_core.provenance.repository import RepositoryTree

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

META_FILE: Final = "meta.json"
TAR_FILE: Final = "repo.tar"
# The paths at the fetched commit (``git ls-tree``), what a citation of a file in the repository is checked against.
TREE_FILE: Final = "tree.json"
TMP_PREFIX: Final = ".tmp-"
# What an entry's tar is, recorded in its meta. 2 (WP-110): every entry belongs to the sandbox user, the only owner git
# in the sandbox reads a repository of on every version the images ship. An entry without it (a worker before WP-110)
# is root's, which git refuses as dubious ownership, so it is a miss and is fetched again.
HAND_OFF_LAYOUT: Final = 2
_LOG = HarnessLog.named("harness_core.repos.cache")


def cache_key(org_id: str, url: str, ref: str | None, *, full: bool) -> str:
    return hashlib.sha256("\0".join((org_id, url.lower(), ref or "", "full" if full else "shallow")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CachedRepository:
    key: str
    tar: Path
    size_bytes: int
    head: str
    ref: str | None
    fetched_at: float
    # Whether the fetch dialled a public address (WP-83); ``None``: an entry from before it was recorded.
    public: bool | None = None
    # The address the fetch dialled (WP83-FU-3: an org's declared ranges are checked against it on every reuse);
    # ``None``: an entry from before it was recorded.
    address: str | None = None


class RepositoryCache:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        max_age_s: float,
        clock: Callable[[], float] = time.time,
        log: HarnessLog = _LOG,
    ) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.max_age_s = max_age_s
        self._clock = clock
        self._log = log
        self._pins: dict[str, int] = {}
        self._ready = False

    def prepare(self) -> None:
        """Creates the folder (owner-only) and removes what a crashed fetch left; once."""
        if self._ready:
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for child in self.root.iterdir():
            if child.name.startswith(TMP_PREFIX):
                shutil.rmtree(child, ignore_errors=True)
        self._ready = True

    def temporary(self) -> Path:
        self.prepare()
        path = self.root / f"{TMP_PREFIX}{os.urandom(8).hex()}"
        path.mkdir(mode=0o700)
        return path

    def _meta(self, key: str) -> dict[str, object] | None:
        try:
            loaded = json.loads((self.root / key / META_FILE).read_text())
        except (OSError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _entry(self, key: str, meta: dict[str, object]) -> CachedRepository | None:
        size, head, fetched = meta.get("bytes"), meta.get("head"), meta.get("fetchedAt")
        ref = meta.get("ref")
        if meta.get("layout") != HAND_OFF_LAYOUT:
            return None
        if not isinstance(size, int) or not isinstance(head, str) or not isinstance(fetched, (int, float)):
            return None
        public, address = meta.get("publicAddress"), meta.get("address")
        return CachedRepository(
            key, self.root / key / TAR_FILE, size, head, ref if isinstance(ref, str) else None, float(fetched),
            public if isinstance(public, bool) else None, address if isinstance(address, str) else None,
        )

    def get(self, key: str, *, head: str | None = None) -> CachedRepository | None:
        """A fresh entry, marked as just used; ``None`` when there is none or it is too old to reuse. ``head``: the
        commit a session already holds as a reference (ADR-RUN-01 D2); an entry at that commit is reused however old,
        since it is exactly what the session read."""
        self.prepare()
        meta = self._meta(key)
        entry = self._entry(key, meta) if meta is not None else None
        if entry is None or not entry.tar.is_file():
            return None
        if self._clock() - entry.fetched_at > self.max_age_s and (head is None or entry.head != head):
            return None
        meta = {**(meta or {}), "usedAt": self._clock()}
        with contextlib.suppress(OSError):
            (self.root / key / META_FILE).write_text(json.dumps(meta))
        return entry

    def tree_of(self, label: str, head: str) -> RepositoryTree | None:
        """The files and folders at ``head`` of the repository ``label`` names (``host/owner/name``), from any entry
        that fetched that commit; ``None`` when none kept its listing (an entry from before WP-96, or evicted)."""
        for child in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if child.name.startswith(TMP_PREFIX) or not child.is_dir():
                continue
            meta = self._meta(child.name)
            if meta is None or meta.get("label") != label or meta.get("head") != head:
                continue
            try:
                loaded = json.loads((child / TREE_FILE).read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(loaded, dict):
                continue
            files, folders = loaded.get("files"), loaded.get("folders")
            if isinstance(files, list) and isinstance(folders, list):
                return RepositoryTree(
                    frozenset(f for f in files if isinstance(f, str)), frozenset(f for f in folders if isinstance(f, str))
                )
        return None

    def known_size(self, key: str) -> int | None:
        """The size of the last fetch of this key, however old: what the approval card says the clone is about."""
        meta = self._meta(key)
        size = meta.get("bytes") if meta is not None else None
        return size if isinstance(size, int) else None

    def put(
        self,
        key: str,
        staged: Path,
        *,
        head: str,
        ref: str | None,
        label: str,
        public: bool = False,
        address: str | None = None,
    ) -> CachedRepository:
        """Moves ``staged`` (a folder holding ``repo.tar``) into place as ``key``'s entry, then evicts to the cap."""
        tar = staged / TAR_FILE
        size = tar.stat().st_size
        now = self._clock()
        meta = {
            "key": key, "label": label, "head": head, "ref": ref, "bytes": size, "fetchedAt": now, "usedAt": now,
            "publicAddress": public, "address": address, "layout": HAND_OFF_LAYOUT,
        }
        (staged / META_FILE).write_text(json.dumps(meta))
        target = self.root / key
        with self.pinned(key):
            if target.exists():
                old = self.root / f"{TMP_PREFIX}{os.urandom(8).hex()}"
                target.rename(old)
                shutil.rmtree(old, ignore_errors=True)
            staged.rename(target)
            self.evict()
        return CachedRepository(key, target / TAR_FILE, size, head, ref, now, public, address)

    @contextlib.contextmanager
    def pinned(self, key: str) -> Iterator[None]:
        """While held, ``key``'s entry is never evicted (a hand-off is streaming it)."""
        self._pins[key] = self._pins.get(key, 0) + 1
        try:
            yield
        finally:
            left = self._pins[key] - 1
            if left:
                self._pins[key] = left
            else:
                del self._pins[key]

    def entries(self) -> list[tuple[str, int, float]]:
        """``(key, bytes, usedAt)`` of every entry."""
        found: list[tuple[str, int, float]] = []
        for child in self.root.iterdir():
            if child.name.startswith(TMP_PREFIX) or not child.is_dir():
                continue
            meta = self._meta(child.name)
            size = meta.get("bytes") if meta is not None else None
            used = meta.get("usedAt") if meta is not None else None
            found.append((child.name, size if isinstance(size, int) else 0, float(used) if isinstance(used, (int, float)) else 0.0))
        return found

    def total_bytes(self) -> int:
        return sum(size for _, size, _ in self.entries())

    def evict(self) -> int:
        """Removes the least recently used unpinned entries until the total is within ``max_bytes``; how many."""
        entries = sorted(self.entries(), key=lambda e: e[2])
        total = sum(size for _, size, _ in entries)
        removed = 0
        for key, size, _ in entries:
            if total <= self.max_bytes:
                break
            if key in self._pins:
                continue
            shutil.rmtree(self.root / key, ignore_errors=True)
            total -= size
            removed += 1
        if removed:
            self._log.info("repository.cache_evicted", entries=removed, totalBytes=total)
        return removed


__all__ = ["CachedRepository", "RepositoryCache", "cache_key"]
