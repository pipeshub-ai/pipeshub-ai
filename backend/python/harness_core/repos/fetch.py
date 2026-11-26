"""The fetch itself (WP-63 part A): a hostile repository cloned by the worker, never by the sandbox, never checked out.

Every repository is treated as hostile, and so is its host:

- **where it goes**: the host is resolved once and every answer checked as the egress proxy checks a destination
  (public, or opened by the deployment's ``EGRESS_PRIVATE_ALLOW``; never loopback, link-local, metadata, a blocked
  range or PipesHub's own networks). git then dials only the checked address (``http.curloptResolve``), so a second
  lookup cannot move it, and never follows a redirect (``http.followRedirects=false``): one is refused, not chased.
- **what runs**: the pinned git binary (``git_bin``), with no system, global or user config (``GIT_CONFIG_NOSYSTEM``,
  ``GIT_CONFIG_GLOBAL=/dev/null``, an empty ``HOME`` and ``XDG_CONFIG_HOME``), no credential helper and no prompt, no
  proxy, https only (``protocol.allow=never``, ``protocol.file.allow=never``), no hooks, templates, submodules or LFS,
  and every object checked as it arrives (``transfer.fsckObjects``).
- **how much**: a bare clone (no working tree anywhere on the host), shallow (``--depth 1``) unless the whole history
  is asked for, one branch, under a time limit and a size cap the folder is watched against; past either the clone is
  killed and nothing is kept.

The result is a tar of the bare repository in the deployment's cache (``cache.RepositoryCache``), which the worker
hands to the run's sandbox; the sandbox clones from it and checks out there, where a filter or a link acts only on
the sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import re
import shutil
import signal
import tarfile
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final

from harness_core.logs import HarnessLog
from harness_core.provenance.repository import RepositoryTree
from harness_core.repos.cache import TAR_FILE, TREE_FILE, RepositoryCache, cache_key
from harness_core.repos.location import (
    HTTPS_PORT,
    RepositoryLocation,
    RepositoryRefused,
)
from harness_core.sandbox.egress.errors import (
    AddressRefused,
    EgressRefused,
    HostUnresolvable,
)
from harness_core.sandbox.egress.policy import public_addresses
from harness_core.sandbox.egress.types import Target
from harness_core.sandbox.egress.upstream_proxy import private_addresses
from harness_core.security.reachability import PublicReachability
from harness_core.workspace.sync.archive import Owner
from harness_core.workspace.sync.storage_classes import REFERENCES_DIR

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from harness_core.sandbox.egress.policy import Network
    from harness_core.sandbox.egress.ports import HostResolver
    from harness_core.sandbox.egress.upstream_proxy import PrivateAllow

MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB
# The free disk a clone must leave on the cache's volume (the deployment's disk is shared): 5% of the volume, never less
# than 2 GB nor more than 20 GB, so a laptop keeps a margin and a large server is not held back by a flat share.
RESERVE_SHARE: Final = 0.05
RESERVE_MIN: Final = 2 * GIB
RESERVE_MAX: Final = 20 * GIB
# http.curloptResolve arrived in git 2.37: an older git could not be held to the checked address.
MIN_GIT: Final = (2, 37)
# Where the sandbox reads handed-over repositories: their own read-only mount (``EngineControllerConfig.repos_mount``).
HAND_OFF_DIR: Final = "/pipeshub/repos"
# What the folder holds beyond the largest repository: the tar's own folders and the file system's bookkeeping.
HAND_OFF_SLACK_MB: Final = 16
_WATCH_S: Final = 0.2
_HARDENED: Final = (
    ("protocol.allow", "never"),
    ("protocol.https.allow", "always"),
    ("protocol.file.allow", "never"),
    ("protocol.ext.allow", "never"),
    ("http.followRedirects", "false"),
    ("http.proxy", ""),
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("core.askPass", ""),
    ("credential.helper", ""),
    ("submodule.recurse", "false"),
    ("transfer.fsckObjects", "true"),
    ("fetch.fsckObjects", "true"),
    ("init.templateDir", ""),
    ("filter.lfs.process", ""),
    ("filter.lfs.smudge", ""),
    ("filter.lfs.required", "false"),
)


@dataclass(frozen=True, slots=True)
class FetchConfig:
    """``HARNESS_REPO_*``: the cache, its caps, how many fetches run at once and how long one may take."""

    cache_dir: Path
    git_bin: str = "/usr/bin/git"
    cache_max_bytes: int = 2048 * MIB
    repo_max_bytes: int = 500 * MIB
    concurrency: int = 2
    timeout_s: float = 300.0
    max_age_s: float = 600.0
    # The authority bundle the host's certificate is checked with instead of the system's (an internal mirror with
    # its own authority, a test host); unset: the system's.
    ca_file: str | None = None
    # The port every git host is reached on: 443, as the URL check holds it. Only a test rig without a privileged
    # port sets another.
    port: int = HTTPS_PORT
    # ``HARNESS_REPO_CACHE_MIN_FREE_MB``: the reserve, in place of the bounded share; unset: ``disk_reserve``'s.
    min_free_bytes: int | None = None


def disk_reserve(total: int, min_free_bytes: int | None = None) -> int:
    """The free bytes a volume of ``total`` bytes keeps: the deployment's setting, else clamp(5%, 2 GB, 20 GB)."""
    if min_free_bytes is not None:
        return min_free_bytes
    return min(max(int(total * RESERVE_SHARE), RESERVE_MIN), RESERVE_MAX)


def room_for(total: int, free: int, need: int, min_free_bytes: int | None = None) -> bool:
    """Whether ``need`` more bytes leave the volume at or above its reserve."""
    return total > 0 and free - need >= disk_reserve(total, min_free_bytes)


def _gb(count: int) -> str:
    return f"{count / GIB:.1f} GB"


@dataclass(frozen=True, slots=True)
class FetchedRepository:
    location: RepositoryLocation
    key: str
    tar: Path
    size_bytes: int
    head: str
    ref: str | None
    full: bool
    # Served from the cache: nothing moved over the network for this call.
    cached: bool
    # Fetched from a public address with no credential (WP-83): anyone may read it, so it is public content. A mirror
    # the deployment opened (``EGRESS_PRIVATE_ALLOW``) is not: it may hold what only the organization may read.
    public: bool = False
    # The address git dialled, which the org's classifier checks its declared ranges against (WP83-FU-3); ``None``: a
    # cache entry from before it was recorded.
    address: str | None = None

    @property
    def hand_off_name(self) -> str:
        """The folder the sandbox reads it in, under the hand-off directory: one per fetch key."""
        return f"{self.key[:20]}.git"


def git_version(text: str) -> tuple[int, int] | None:
    found = re.search(r"git version (\d+)\.(\d+)", text)
    return (int(found[1]), int(found[2])) if found else None


def git_config_args(address: str, host: str, ca_file: str | None, port: int = HTTPS_PORT) -> list[str]:
    """``-c`` pairs for one fetch: the hardened config, the pinned address, and the authority if configured."""
    pinned = f"[{address}]" if ":" in address else address
    pairs = [*_HARDENED, ("http.curloptResolve", f"{host}:{port}:{pinned}")]
    if ca_file:
        pairs.append(("http.sslCAInfo", ca_file))
    args: list[str] = []
    for key, value in pairs:
        args += ["-c", f"{key}={value}"]
    return args


def git_env(home: Path) -> dict[str, str]:
    """The fetch's whole environment: nothing of the worker's (no proxy, token or config) reaches git."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "LC_ALL": "C",
    }


def clone_argv(
    config: FetchConfig, location: RepositoryLocation, address: str, destination: Path, *, ref: str | None, full: bool
) -> list[str]:
    argv = [config.git_bin, *git_config_args(address, location.host, config.ca_file, config.port)]
    argv += ["clone", "--bare", "--quiet"]
    argv += ["--template=", "--no-tags", "--single-branch", "--no-recurse-submodules"]
    if not full:
        argv += ["--depth", "1"]
    if ref is not None:
        argv += ["--branch", ref]
    url = location.url if config.port == HTTPS_PORT else f"https://{location.host}:{config.port}/{location.path}.git"
    return [*argv, "--", url, str(destination)]


def _folder_bytes(path: Path) -> int:
    total = 0
    for directory, _, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(directory, name)).st_size
    return total


def failure_of(stderr: str) -> RepositoryRefused:
    """git's own words as a plain refusal; its stderr is never shown (it may echo the host's pages)."""
    text = stderr.lower()
    if re.search(r"returned error: 30[0-9]|redirect", text):
        return RepositoryRefused(
            "repository_redirect", "The git host answered with a redirect, which is never followed. Use the repository's own URL."
        )
    if re.search(r"returned error: (401|403|404)|authentication|not found|could not read username", text):
        return RepositoryRefused(
            "repository_not_found",
            "The repository does not exist or is private. Only public repositories can be cloned this way; a private one "
            "is connected as the run's repository.",
        )
    if "remote branch" in text and "not found" in text:
        return RepositoryRefused("repository_ref_not_found", "That branch or tag does not exist in the repository.")
    if "fsck" in text or "bad object" in text or "malformed" in text:
        return RepositoryRefused("repository_corrupt", "The repository sent objects that failed git's checks; nothing was kept.")
    if "ssl" in text or "certificate" in text or "tls" in text:
        return RepositoryRefused("repository_tls_failed", "The git host's certificate could not be verified.")
    return RepositoryRefused("repository_fetch_failed", "The repository could not be fetched from its host.")


def parse_tree_listing(listing: bytes) -> RepositoryTree:
    """``git ls-tree -r -t -z``'s output (``<mode> <type> <object>\t<path>`` records) as the commit's files and
    folders."""
    files: set[str] = set()
    folders: set[str] = set()
    for record in listing.split(b"\0"):
        meta, _, raw = record.partition(b"\t")
        kind = meta.split(b" ")[1] if meta.count(b" ") >= 2 else b""
        path = raw.decode("utf-8", "replace")
        if not path:
            continue
        if kind == b"tree":
            folders.add(path)
        elif kind == b"blob":
            files.add(path)
    return RepositoryTree(frozenset(files), frozenset(folders))


def _tar_bare(bare: Path, out: Path, name: str, owner: Owner | None = None) -> None:
    """``<name>/…``: the bare repository, no link or device (git wrote it here). Its entries belong to the sandbox user
    (WP-110): git reads a local repository only of its own user's, and the ``safe.directory`` that would name another
    never reaches the ``upload-pack`` a clone of it runs on git 2.39. It stays read-only: the mount is, so not even its
    owner can change a mode or add a file (EROFS)."""
    owner = owner or Owner()
    with tarfile.open(out, mode="w") as archive:
        for directory, folders, files in os.walk(bare):
            folders.sort()
            rel = os.path.relpath(directory, bare)
            arc = name if rel == "." else f"{name}/{rel}"
            info = tarfile.TarInfo(arc)
            info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, owner.uid, owner.gid
            archive.addfile(info)
            for file in sorted(files):
                path = os.path.join(directory, file)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                info = tarfile.TarInfo(f"{arc}/{file}")
                info.size, info.mode, info.uid, info.gid = os.path.getsize(path), 0o444, owner.uid, owner.gid
                with open(path, "rb") as handle:
                    archive.addfile(info, handle)


# A cache folder the worker may not write: a volume another uid owns, or one mounted read-only.
_UNWRITABLE: Final = frozenset({errno.EACCES, errno.EPERM, errno.EROFS})


def _nearest_existing(path: Path) -> Path:
    while not os.path.isdir(path) and path.parent != path:
        path = path.parent
    return path


@dataclass
class RepositoryFetcher:
    """Fetches into the cache, ``config.concurrency`` at a time, one per key at a time."""

    config: FetchConfig
    resolver: HostResolver
    private_allow: tuple[PrivateAllow, ...] = ()
    refused_networks: tuple[Network, ...] = ()
    log: HarnessLog = field(default_factory=lambda: HarnessLog.named("harness_core.repos.fetch"))
    cache: RepositoryCache = field(init=False)
    _slots: asyncio.Semaphore = field(init=False)
    _locks: dict[str, asyncio.Lock] = field(init=False, default_factory=dict[str, asyncio.Lock])
    _git_checked: bool = field(init=False, default=False)
    # The volume's (total, free) bytes; ``shutil.disk_usage`` of the cache folder unless a test sets another.
    disk_usage: Callable[[Path], tuple[int, int]] | None = None

    def __post_init__(self) -> None:
        self.cache = RepositoryCache(
            self.config.cache_dir, max_bytes=self.config.cache_max_bytes, max_age_s=self.config.max_age_s, log=self.log
        )
        self._slots = asyncio.Semaphore(max(1, self.config.concurrency))

    def _disk(self) -> tuple[int, int]:
        if self.disk_usage is not None:
            return self.disk_usage(self.config.cache_dir)
        # Measured from the nearest folder that exists, never by creating the cache: a root-owned volume
        # made that a PermissionError, which read as a full disk.
        usage = shutil.disk_usage(_nearest_existing(self.config.cache_dir))
        return usage.total, usage.free

    def cache_unwritable(self) -> bool:
        """Whether the worker cannot create or write its cache folder (another uid's volume, a read-only mount)."""
        try:
            self.cache.prepare()
        except OSError as exc:
            if exc.errno in _UNWRITABLE:
                return True
            raise
        return not os.access(self.config.cache_dir, os.W_OK | os.X_OK)

    def _reserve_of(self, total: int) -> int:
        return disk_reserve(total, self.config.min_free_bytes)

    def disk_low(self) -> bool:
        """Whether the cache's volume is within twice its reserve of full: what the admin is warned about."""
        try:
            total, free = self._disk()
        except OSError:
            return True
        return total <= 0 or free < 2 * self._reserve_of(total)

    def _below_reserve(self) -> bool:
        """Whether the volume is already under its reserve (checked while a clone writes)."""
        try:
            total, free = self._disk()
        except OSError:
            return True
        return total <= 0 or free < self._reserve_of(total)

    def status(self) -> dict[str, int | bool]:
        """The cache as an admin's Git hosts screen shows it (``context.deployment.cache``)."""
        try:
            unwritable = self.cache_unwritable()
        except OSError:
            unwritable = False
        try:
            used = self.cache.total_bytes() if self.config.cache_dir.exists() else 0
        except OSError:
            used = 0
        try:
            total, free = self._disk()
        except OSError:
            total, free = 0, 0
        return {
            "totalMb": self.config.cache_max_bytes // MIB,
            "perRepoMb": self.config.repo_max_bytes // MIB,
            "concurrency": max(1, self.config.concurrency),
            "usedMb": -(-used // MIB),
            "freeMb": free // MIB,
            "reserveMb": self._reserve_of(total) // MIB,
            "diskLow": self.disk_low(),
            "cacheUnwritable": unwritable,
        }

    def check_disk(self, need: int) -> None:
        """Refuses a clone of up to ``need`` bytes that would leave the volume under its reserve."""
        try:
            total, free = self._disk()
        except OSError:
            total, free = 0, 0
        if room_for(total, free, need, self.config.min_free_bytes):
            return
        self.log.warning("repository.disk_low")
        raise RepositoryRefused(
            "repository_disk_low",
            f"The server has {_gb(free)} free and keeps {_gb(self._reserve_of(total))} for itself, so a repository of up "
            f"to {need // MIB} MB cannot be fetched now. An admin can free space or give the repository cache a larger "
            "disk; repositories fetched before still work.",
        )

    def key_for(self, org_id: str, location: RepositoryLocation, ref: str | None, *, full: bool) -> str:
        return cache_key(org_id, location.url, ref, full=full)

    def known_size(self, org_id: str, location: RepositoryLocation, ref: str | None, *, full: bool) -> int | None:
        return self.cache.known_size(self.key_for(org_id, location, ref, full=full))

    async def checked_address(self, host: str) -> str:
        """The one address git may dial for ``host``, checked as the egress proxy checks a destination."""
        try:
            answers = await self.resolver.resolve(host)
        except AddressRefused:
            raise RepositoryRefused("repository_address_refused", f"{host} points to an address that is never reached.") from None
        except HostUnresolvable:
            raise RepositoryRefused("repository_unreachable", f"{host} could not be resolved.") from None
        target = Target(host, self.config.port, "/")
        try:
            if self.private_allow:
                allowed = private_addresses(target, answers, self.private_allow, self.refused_networks)
            else:
                allowed = public_addresses(target, answers, self.refused_networks)
        except EgressRefused:
            raise RepositoryRefused(
                "repository_address_refused", f"{host} points to a private or internal address, which is never reached."
            ) from None
        return allowed[0]

    def address_public(self, address: str) -> bool:
        """Whether the one address git dials is public (WP-83), by the deployment's ranges alone: an org's declared
        ones are its ``PublicReachability``'s to apply, at each call (WP83-FU-3)."""
        return PublicReachability(refused=self.refused_networks).address_public(address)

    async def _check_git(self) -> None:
        if self._git_checked:
            return
        try:
            process = await asyncio.create_subprocess_exec(
                self.config.git_bin, "--version", env={"PATH": "/usr/bin:/bin"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(process.communicate(), 10)
        except (OSError, TimeoutError) as exc:
            # WP-81: a worker without git: the person read "not available" and the operator read nothing.
            self.log.error(
                "repository.git_unavailable", gitBin=self.config.git_bin, errorType=type(exc).__name__,
                errno=getattr(exc, "errno", None),
            )
            raise RepositoryRefused("repository_unavailable", "Repository fetch is not available on this deployment.") from None
        version = git_version(out.decode("ascii", "replace"))
        if version is None or version < MIN_GIT:
            self.log.error("repository.git_too_old", version=str(version))
            raise RepositoryRefused("repository_unavailable", "Repository fetch is not available on this deployment.")
        self._git_checked = True

    async def fetch(
        self,
        org_id: str,
        location: RepositoryLocation,
        *,
        ref: str | None,
        full: bool,
        max_bytes: int | None = None,
        held: str | None = None,
    ) -> FetchedRepository:
        """The repository in the cache (fetched now, or a fresh entry reused). ``max_bytes``: a tighter cap than the
        deployment's per-repository one (what is left of the run's outside-traffic cap). ``held``: the commit the
        session holds it at as a reference; the cache's entry at that commit is reused at any age."""
        key = self.key_for(org_id, location, ref, full=full)
        cap = self.config.repo_max_bytes if max_bytes is None else min(max_bytes, self.config.repo_max_bytes)
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            return await self._fetch_locked(lock, key, location, ref=ref, full=full, cap=cap, held=held)
        except OSError as exc:
            if exc.errno not in _UNWRITABLE:
                raise
            raise self._unwritable(exc) from None

    def _unwritable(self, exc: OSError) -> RepositoryRefused:
        """The worker's uid can fail to create the cache under a root-owned data volume; the person must not read
        the exception. The operator gets the folder, errno and uid; the person, who to ask."""
        self.log.error(
            "repository.cache_unwritable",
            path=str(exc.filename or self.config.cache_dir),
            errno=errno.errorcode.get(exc.errno or 0, str(exc.errno)),
            uid=os.geteuid(),
            cacheDir=str(self.config.cache_dir),
        )
        return RepositoryRefused(
            "repository_cache_unwritable",
            "Repository fetch is not working on this server: it cannot save repositories to its cache folder. Ask an "
            "administrator to make the server's data folder writable for PipesHub (the worker's log names the "
            "folder), then try again. Nothing was fetched.",
        )

    async def _fetch_locked(
        self,
        lock: asyncio.Lock,
        key: str,
        location: RepositoryLocation,
        *,
        ref: str | None,
        full: bool,
        cap: int,
        held: str | None = None,
    ) -> FetchedRepository:
        async with lock:
            hit = await asyncio.to_thread(partial(self.cache.get, key, head=held))
            if hit is not None:
                # An entry from before the address was recorded came from a public one unless the deployment opens any.
                public = hit.public if hit.public is not None else not self.private_allow
                return FetchedRepository(
                    location, key, hit.tar, hit.size_bytes, hit.head, ref, full, cached=True, public=public,
                    address=hit.address,
                )
            await self._check_git()
            known = await asyncio.to_thread(self.cache.known_size, key)
            self.check_disk(cap if known is None else min(known, cap))
            async with self._slots:
                return await self._fetch(key, location, ref=ref, full=full, cap=cap)

    async def _fetch(self, key: str, location: RepositoryLocation, *, ref: str | None, full: bool, cap: int) -> FetchedRepository:
        address = await self.checked_address(location.host)
        public = self.address_public(address)
        staged = await asyncio.to_thread(self.cache.temporary)
        try:
            home = staged / "home"
            home.mkdir(mode=0o700)
            bare = staged / "repo.git"
            await self._clone(clone_argv(self.config, location, address, bare, ref=ref, full=full), bare, home, cap)
            head = await self._head(bare, home)
            # ADR-RUN-01 D3: the paths at the fetched commit, so a link into the repository is cited only for a path
            # that exists there. Best effort: without the listing no link into the repository is cited.
            await self._write_tree(bare, home, staged / TREE_FILE)
            name = f"{key[:20]}.git"
            await asyncio.to_thread(_tar_bare, bare, staged / TAR_FILE, name)
            shutil.rmtree(bare, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)
            if (staged / TAR_FILE).stat().st_size > cap:
                raise self._too_large(cap)
            entry = await asyncio.to_thread(
                self.cache.put, key, staged, head=head, ref=ref, label=location.label, public=public, address=address
            )
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        self.log.info("repository.fetched", host=location.host, bytes=entry.size_bytes, full=full)
        return FetchedRepository(
            location, key, entry.tar, entry.size_bytes, head, ref, full, cached=False, public=public, address=address
        )

    @staticmethod
    def _too_large(cap: int) -> RepositoryRefused:
        return RepositoryRefused(
            "repository_too_large",
            f"The repository is larger than {cap // MIB} MB, the most one clone may bring; nothing was kept. "
            "Ask for the default (latest commit only) rather than the full history, or a smaller repository.",
        )

    async def _clone(self, argv: list[str], bare: Path, home: Path, cap: int) -> None:
        process = await asyncio.create_subprocess_exec(
            *argv, env=git_env(home), cwd=str(home), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        assert process.stderr is not None
        err_task = asyncio.ensure_future(process.stderr.read(64 * 1024))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.timeout_s
        refusal: RepositoryRefused | None = None
        try:
            while process.returncode is None:
                with contextlib.suppress(TimeoutError):
                    _ = await asyncio.wait_for(asyncio.shield(process.wait()), _WATCH_S)
                if process.returncode is not None:
                    break
                if loop.time() > deadline:
                    refusal = RepositoryRefused(
                        "repository_timeout", f"The clone took longer than {int(self.config.timeout_s)} seconds; nothing was kept."
                    )
                elif await asyncio.to_thread(self._below_reserve):
                    refusal = RepositoryRefused(
                        "repository_disk_low",
                        "The server ran low on disk space during the clone; nothing was kept.",
                    )
                elif await asyncio.to_thread(_folder_bytes, bare) > cap:
                    refusal = self._too_large(cap)
                if refusal is not None:
                    _kill(process)
                    _ = await process.wait()
                    raise refusal
        except asyncio.CancelledError:
            _kill(process)
            raise
        stderr = (await err_task).decode("utf-8", "replace")
        if process.returncode != 0:
            failure = failure_of(stderr)
            self.log.info("repository.fetch_failed", reasonCode=failure.reason, exitCode=process.returncode)
            raise failure
        if await asyncio.to_thread(_folder_bytes, bare) > cap:
            raise self._too_large(cap)

    async def _write_tree(self, bare: Path, home: Path, out: Path) -> None:
        process = await asyncio.create_subprocess_exec(
            self.config.git_bin, "--git-dir", str(bare), "ls-tree", "-r", "-t", "-z", "HEAD",
            env=git_env(home), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            listing, _ = await asyncio.wait_for(process.communicate(), 60)
        except TimeoutError:
            _kill(process)
            return
        if process.returncode != 0:
            return
        tree = parse_tree_listing(listing)
        await asyncio.to_thread(out.write_text, json.dumps({"files": sorted(tree.files), "folders": sorted(tree.folders)}))

    async def _head(self, bare: Path, home: Path) -> str:
        process = await asyncio.create_subprocess_exec(
            self.config.git_bin, "--git-dir", str(bare), "rev-parse", "HEAD",
            env=git_env(home), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(process.communicate(), 30)
        head = out.decode("ascii", "replace").strip()
        if process.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", head):
            raise RepositoryRefused("repository_empty", "The repository has no commits to clone.")
        return head


def hand_off_mount(config: FetchConfig) -> tuple[str, int]:
    """The repository folder's mount: its target and its size, the per-repository cap and a little slack."""
    return HAND_OFF_DIR, -(-config.repo_max_bytes // MIB) + HAND_OFF_SLACK_MB


def references_mount(config: FetchConfig) -> tuple[str, int]:
    """The references mount (WP98-FU-1, ADR-RUN-02): where ``ph git clone`` checks repositories out, outside the
    synced workspace; its size as a scratch mount, room for a checked-out tree of the largest repository twice over."""
    return REFERENCES_DIR, 2 * -(-config.repo_max_bytes // MIB) + HAND_OFF_SLACK_MB


def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


__all__ = [
    "HAND_OFF_SLACK_MB",
    "REFERENCES_DIR",
    "hand_off_mount",
    "references_mount",
    "HAND_OFF_DIR",
    "MIB",
    "MIN_GIT",
    "FetchConfig",
    "FetchedRepository",
    "RepositoryFetcher",
    "clone_argv",
    "failure_of",
    "git_config_args",
    "git_env",
    "git_version",
]
