#!/usr/bin/env python3
"""Supply-chain rules for PipesHub's dependency locks, installs, images and fetched code (SEC-SC-07..11, SEC-D-11).

    python3 scripts/supply_chain.py check            # offline, every rule; what CI runs on a pull request
    python3 scripts/supply_chain.py lock             # regenerate every Python lock (needs uv)
    python3 scripts/supply_chain.py lock --check     # fail if a fresh resolve would write different locks
    python3 scripts/supply_chain.py verify-hashes    # online: every locked hash is one the index publishes
    python3 scripts/supply_chain.py images           # print the tag-only image report (image-pins.json shape)
    python3 scripts/supply_chain.py installed DIR    # fail if a site-packages dir differs from runtime.lock

Standard library only, plus `packaging`, which falls back to pip's vendored copy: main.yml runs `check`
before anything is installed. docs/agent-harness-v2/guides/SUPPLY-CHAIN.md is the human guide.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:
    from packaging.markers import Marker
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError:  # a bare runner Python: pip ships the same library
    from pip._vendor.packaging.markers import Marker  # type: ignore[no-redef]
    from pip._vendor.packaging.requirements import Requirement  # type: ignore[no-redef]
    from pip._vendor.packaging.utils import canonicalize_name  # type: ignore[no-redef]
    from pip._vendor.packaging.version import Version  # type: ignore[no-redef]

REPO = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))

PY_DIR = Path("backend/python")
REQ_DIR = PY_DIR / "requirements"
# Resolve order matters: dev first (the widest set), then the others constrained by it, so a package has
# one version and one hash list in every lock.
HASHED_LOCKS = ("dev", "runtime", "build", "tools")
VCS_LOCK = REQ_DIR / "vcs.lock"
DENYLIST = Path("contracts/supply-chain/denylist.json")
IMAGE_PINS = Path("contracts/supply-chain/image-pins.json")
INSTALL_SCRIPTS = Path("contracts/supply-chain/install-scripts.json")
UNLOCKED_INSTALLS = Path("contracts/supply-chain/unlocked-installs.json")
#: A build that adds files of its own lists their entries in a file of the same name here, merged into the shared one.
EXTRA_CONTRACTS = Path("contracts/supply-chain/extra")
UV_LOCKS = (Path("integration-tests/uv.lock"),)
SDK_REQ_DIR = Path("integration-tests/sdk_tests/requirements")

PYPI = "https://pypi.org/simple"
TORCH_CPU = "https://download.pytorch.org/whl/cpu"
# uv's --torch-backend routes these to TORCH_CPU; everything else comes from PyPI.
TORCH_PACKAGES = frozenset({"torch", "torchvision", "torchaudio"})
# Days a release must have been public before a lock may first pick it: a poisoned upload is usually found
# and pulled within hours (litellm 1.107.0 was).
COOLDOWN_DAYS = 7
LOCK_FORMAT = "pxh-python-lock/1"
UV_FLAGS = (
    "--universal",
    "--python-version",
    "3.12",
    "--generate-hashes",
    "--default-index",
    PYPI,
    "--index-strategy",
    "first-index",
    "--torch-backend",
    "cpu",
    "--no-header",
    "--quiet",
)
LOCK_INPUTS = {
    "dev": ["pyproject.toml", "--extra", "dev"],
    "runtime": ["pyproject.toml", "-c", "requirements/dev.lock"],
    "build": ["requirements/build.in", "-c", "requirements/dev.lock"],
    "tools": ["requirements/tools.in", "-c", "requirements/dev.lock"],
}
TARGET_ENVIRONMENTS = (
    {"sys_platform": "linux", "platform_system": "Linux", "platform_machine": "x86_64"},
    {"sys_platform": "linux", "platform_system": "Linux", "platform_machine": "aarch64"},
    {"sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": "arm64"},
)
PY312 = {"python_version": "3.12", "python_full_version": "3.12.0", "implementation_name": "cpython",
         "platform_python_implementation": "CPython", "os_name": "posix"}


@dataclass(frozen=True)
class StandaloneLock:
    """A lock resolved on its own from one .in file, beside what installs it, outside the backend project."""

    source: Path
    lock: Path
    install: str
    linux_only: bool


STANDALONE_LOCKS = {
    # The legacy code sandbox image's own Python packages (deployment/sandbox/Dockerfile).
    "sandbox": StandaloneLock(Path("deployment/sandbox/requirements.in"), Path("deployment/sandbox/requirements.lock"),
                              "pip install --require-hashes --no-deps -r requirements.lock", linux_only=True),
    # sdk-tests.yml's provisioning helpers, in a venv of their own (C644-FU-3).
    "sdk-helpers": StandaloneLock(SDK_REQ_DIR / "helpers.in", SDK_REQ_DIR / "helpers.lock",
                                  f"pip install --require-hashes --no-deps -r {SDK_REQ_DIR}/helpers.lock",
                                  linux_only=False),
    # The Python SDK suite's test tools (run_sdk_tests.sh). A lock of its own, not the helpers': the SDK under
    # test resolves its own dependencies, pydantic among them, as its users' installs do.
    "sdk-tests": StandaloneLock(SDK_REQ_DIR / "tests.in", SDK_REQ_DIR / "tests.lock",
                                f"pip install --require-hashes --no-deps -r {SDK_REQ_DIR}/tests.lock",
                                linux_only=False),
}

# Where a package-lock entry may resolve. SheetJS publishes xlsx only on its own CDN; its lock entry carries a
# sha512 like any other, which npm ci checks.
NPM_HOSTS = ("https://registry.npmjs.org/", "https://cdn.sheetjs.com/")
SKIP_DIRS = frozenset({"node_modules", ".git", ".claude", "venv", ".venv", "__pycache__", ".next", "out", "dist"})
_SHA256 = re.compile(r"--hash=sha256:([0-9a-f]{64})\b")
_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(?P<version>[^\s;\\]+)\s*(?:;\s*(?P<marker>.*?))?\s*$")
_VCS = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*@\s*git\+https://[^\s@]+@(?P<commit>[0-9a-f]{40})(?:#\S*)?\s*(?:;\s*(?P<marker>.*))?$"
)


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.path}: {self.message}"


@dataclass
class Entry:
    name: str
    version: str
    marker: str | None
    hashes: tuple[str, ...]
    line: int

    @property
    def key(self) -> str:
        return canonicalize_name(self.name)


@dataclass
class Lock:
    path: str
    header: dict[str, str] = field(default_factory=dict)
    options: list[tuple[int, str]] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    bad: list[tuple[int, str]] = field(default_factory=list)


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Requirement-file lines with continuations joined and comments dropped, numbered by their first line."""
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not buf:
            start = n
        if line.lstrip().startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", line)
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip():
            out.append((start, " ".join(buf.split())))
        buf = ""
    if buf.strip():
        out.append((start, " ".join(buf.split())))
    return out


def parse_lock(path: Path, rel: str) -> Lock:
    text = path.read_text(encoding="utf-8")
    lock = Lock(path=rel)
    for raw in text.splitlines():
        m = re.match(r"^# ([a-z0-9-]+): (\S+)\s*$", raw)
        if m:
            lock.header[m.group(1)] = m.group(2)
        elif raw.strip() and not raw.startswith("#"):
            break
    for n, line in logical_lines(text):
        if line.startswith("-"):
            lock.options.append((n, line))
            continue
        head, _, _ = line.partition(" --hash=")
        hashes = tuple(_SHA256.findall(line))
        m = _PIN.match(head)
        if m is None or not hashes or line.count("--hash=") != len(hashes):
            lock.bad.append((n, line))
            continue
        lock.entries.append(Entry(m.group("name"), m.group("version"), m.group("marker"), hashes, n))
    return lock


def _marker_applies(marker: str | None, env: dict[str, str]) -> bool:
    return marker is None or Marker(marker).evaluate({**PY312, **env})


def _applies_anywhere(marker: str | None) -> bool:
    return any(_marker_applies(marker, env) for env in TARGET_ENVIRONMENTS)


def _norm_version(version: str) -> str:
    return version.split("+", 1)[0]


def _pyproject(repo: Path) -> dict:
    return tomllib.loads((repo / PY_DIR / "pyproject.toml").read_text(encoding="utf-8"))


def fingerprint(repo: Path) -> str:
    """What the locks are resolved from; a change here without `lock` leaves them stale."""
    pp = _pyproject(repo)
    project = pp.get("project", {})
    inputs = {
        "format": LOCK_FORMAT,
        "uv-flags": list(UV_FLAGS),
        "lock-inputs": LOCK_INPUTS,
        "dependencies": project.get("dependencies", []),
        "optional-dependencies": project.get("optional-dependencies", {}),
        "requires-python": project.get("requires-python"),
        "build-system": pp.get("build-system", {}),
        "tool.uv": pp.get("tool", {}).get("uv", {}),
        "build.in": _in_lines(repo / REQ_DIR / "build.in"),
        "tools.in": _in_lines(repo / REQ_DIR / "tools.in"),
    }
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def standalone_fingerprint(repo: Path, name: str) -> str:
    spec = STANDALONE_LOCKS[name]
    inputs = {"format": LOCK_FORMAT, "uv-flags": list(UV_FLAGS), "environments": _environments(linux_only=spec.linux_only),
              "requirements.in": _in_lines(repo / spec.source)}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def _environments(*, linux_only: bool) -> list[str]:
    return [f"python_full_version >= '3.12' and python_full_version < '3.13' and platform_python_implementation == "
            f"'CPython' and sys_platform == '{e['sys_platform']}' and platform_machine == '{e['platform_machine']}'"
            for e in TARGET_ENVIRONMENTS if e["sys_platform"] == "linux" or not linux_only]


def _in_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for _, line in logical_lines(path.read_text(encoding="utf-8"))]


def load_locks(repo: Path) -> tuple[dict[str, Lock], list[Finding]]:
    locks: dict[str, Lock] = {}
    findings: list[Finding] = []
    for name in HASHED_LOCKS:
        rel = str(REQ_DIR / f"{name}.lock")
        path = repo / rel
        if not path.exists():
            findings.append(Finding("PY-LOCK-MISSING", rel, "lock file is missing; run scripts/supply_chain.py lock"))
            continue
        locks[name] = parse_lock(path, rel)
    for name, spec in STANDALONE_LOCKS.items():
        if not (repo / spec.source).exists():
            continue
        if (repo / spec.lock).exists():
            locks[name] = parse_lock(repo / spec.lock, str(spec.lock))
        else:
            findings.append(Finding("PY-LOCK-MISSING", str(spec.lock), "lock file is missing; run scripts/supply_chain.py lock"))
    return locks, findings


def check_python_locks(repo: Path) -> list[Finding]:
    locks, findings = load_locks(repo)
    current = {name: fingerprint(repo) for name in HASHED_LOCKS}
    current |= {name: standalone_fingerprint(repo, name) for name in STANDALONE_LOCKS}
    for name, lock in locks.items():
        for n, line in lock.bad:
            findings.append(Finding("PY-LOCK-HASH", f"{lock.path}:{n}",
                                    f"not an exact pin with sha256 hashes: {line[:120]}"))
        for n, opt in lock.options:
            # install.sh names the indexes; a lock that also could would steer the other locks' resolves too.
            findings.append(Finding("PY-LOCK-INDEX", f"{lock.path}:{n}", f"option not allowed in a lock: {opt}"))
        if lock.header.get("inputs-sha256") != current[name]:
            findings.append(Finding("PY-LOCK-STALE", lock.path,
                                    "what this lock is resolved from (pyproject.toml, a requirements .in file) changed "
                                    "since it was written; run python3 scripts/supply_chain.py lock"))
        seen: dict[tuple[str, str | None], int] = {}
        for e in lock.entries:
            k = (e.key, e.marker)
            if k in seen:
                findings.append(Finding("PY-LOCK-HASH", f"{lock.path}:{e.line}", f"{e.name} pinned twice"))
            seen[k] = e.line
    findings += _check_vcs(repo)
    findings += _check_uv_locks(repo)
    findings += _check_agreement(repo, locks)
    findings += _check_subsets(locks)
    return findings


def _check_uv_locks(repo: Path) -> list[Finding]:
    """A uv project lock (installed with `uv sync --locked`) carries a sha256 for every file it can fetch."""
    out = []
    for rel in UV_LOCKS:
        path = repo / rel
        if not path.exists():
            continue
        for pkg in tomllib.loads(path.read_text(encoding="utf-8")).get("package", []):
            source = pkg.get("source", {})
            if "git" in source:
                if not re.search(r"#[0-9a-f]{40}$", source["git"]):
                    out.append(Finding("PY-LOCK-VCS", str(rel), f"{pkg['name']} is a git source without a commit id"))
                continue
            if "registry" not in source:
                continue
            files = [pkg["sdist"]] if "sdist" in pkg else []
            files += pkg.get("wheels", [])
            if not files or any(not str(f.get("hash", "")).startswith("sha256:") for f in files):
                out.append(Finding("PY-LOCK-HASH", str(rel), f"{pkg['name']}=={pkg.get('version')} has a file without a sha256"))
    return out


def _check_vcs(repo: Path) -> list[Finding]:
    path = repo / VCS_LOCK
    if not path.exists():
        return [Finding("PY-LOCK-MISSING", str(VCS_LOCK), "lock file is missing; run scripts/supply_chain.py lock")]
    out = []
    for n, line in logical_lines(path.read_text(encoding="utf-8")):
        if not _VCS.match(line):
            out.append(Finding("PY-LOCK-VCS", f"{VCS_LOCK}:{n}",
                               f"a source requirement must be git+https pinned to a full commit id: {line[:120]}"))
    return out


def _vcs_names(repo: Path) -> dict[str, str]:
    path = repo / VCS_LOCK
    if not path.exists():
        return {}
    out = {}
    for _, line in logical_lines(path.read_text(encoding="utf-8")):
        m = _VCS.match(line)
        if m:
            out[canonicalize_name(m.group("name"))] = m.group("commit")
    return out


def _check_agreement(repo: Path, locks: dict[str, Lock]) -> list[Finding]:
    """Every direct requirement is locked at a version its specifier admits, and nothing excluded is locked."""
    pp = _pyproject(repo)
    project = pp.get("project", {})
    excluded = {canonicalize_name(n) for n in pp.get("tool", {}).get("uv", {}).get("exclude-dependencies", [])}
    vcs = _vcs_names(repo)
    findings = []
    wanted = {
        "runtime": list(project.get("dependencies", [])),
        "dev": list(project.get("dependencies", [])) + list(project.get("optional-dependencies", {}).get("dev", [])),
        "build": _in_lines(repo / REQ_DIR / "build.in"),
        "tools": _in_lines(repo / REQ_DIR / "tools.in"),
        **{name: _in_lines(repo / spec.source) for name, spec in STANDALONE_LOCKS.items()},
    }
    for lock_name, reqs in wanted.items():
        lock = locks.get(lock_name)
        if lock is None:
            continue
        by_name: dict[str, list[Entry]] = {}
        for e in lock.entries:
            by_name.setdefault(e.key, []).append(e)
        for spec in reqs:
            req = Requirement(spec)
            key = canonicalize_name(req.name)
            if req.marker is not None and not _applies_anywhere(str(req.marker)):
                continue
            if req.url:
                commit = re.search(r"@([0-9a-f]{40})(?:#|$)", req.url)
                if key not in vcs or commit is None or vcs[key] != commit.group(1):
                    findings.append(Finding("PY-LOCK-AGREE", lock.path,
                                            f"{req.name} is a source requirement; vcs.lock must hold the same commit"))
                continue
            entries = by_name.get(key)
            if not entries:
                findings.append(Finding("PY-LOCK-AGREE", lock.path, f"{req.name} is required but not locked"))
                continue
            for e in entries:
                if req.specifier and not req.specifier.contains(Version(e.version), prereleases=True):
                    findings.append(Finding("PY-LOCK-AGREE", f"{lock.path}:{e.line}",
                                            f"{e.name}=={e.version} does not satisfy {spec}"))
        for e in lock.entries:
            if e.key in excluded:
                findings.append(Finding("PY-LOCK-AGREE", f"{lock.path}:{e.line}",
                                        f"{e.name} is in [tool.uv] exclude-dependencies but locked"))
    return findings


def _check_subsets(locks: dict[str, Lock]) -> list[Finding]:
    dev = locks.get("dev")
    if dev is None:
        return []
    pinned = {(e.key, e.marker): e for e in dev.entries}
    by_key = {e.key: e for e in dev.entries}
    findings = []
    for name in ("runtime", "build", "tools"):
        lock = locks.get(name)
        if lock is None:
            continue
        for e in lock.entries:
            d = pinned.get((e.key, e.marker)) or by_key.get(e.key)
            if d is None:
                if name == "runtime":
                    findings.append(Finding("PY-LOCK-SUBSET", f"{lock.path}:{e.line}", f"{e.name} is not in dev.lock"))
                continue
            if d.version != e.version or (name == "runtime" and set(d.hashes) != set(e.hashes)):
                findings.append(Finding("PY-LOCK-SUBSET", f"{lock.path}:{e.line}",
                                        f"{e.name}=={e.version} differs from dev.lock's {d.version}"))
    return findings


# --- denylist ------------------------------------------------------------------------------------------------


def load_denylist(repo: Path) -> dict[str, dict[str, set[str]]]:
    path = repo / DENYLIST
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, set[str]]] = {"python": {}, "npm": {}}
    for eco in out:
        for item in data.get(eco, []):
            key = canonicalize_name(item["name"]) if eco == "python" else item["name"]
            out[eco].setdefault(key, set()).update(item["versions"])
    return out


# confirmed: PipesHub saw it or the publisher confirmed it; verify: a public report not yet checked. Both are denied.
DENYLIST_STATUSES = ("confirmed", "verify")


def check_denylist(repo: Path) -> list[Finding]:
    if not (repo / DENYLIST).exists():
        return [Finding("DENYLIST", str(DENYLIST), "denylist file is missing")]
    deny = load_denylist(repo)
    findings = []
    data = json.loads((repo / DENYLIST).read_text(encoding="utf-8"))
    for eco in ("python", "npm"):
        for item in data.get(eco, []):
            if item.get("status") not in DENYLIST_STATUSES:
                findings.append(Finding("DENYLIST", str(DENYLIST), f"{item.get('name')} {item.get('versions')}: status "
                                        f"must be one of {', '.join(DENYLIST_STATUSES)}"))
    py = deny["python"]
    locks, _ = load_locks(repo)
    for lock in locks.values():
        for e in lock.entries:
            if _norm_version(e.version) in py.get(e.key, ()) or e.version in py.get(e.key, ()):
                findings.append(Finding("DENYLIST", f"{lock.path}:{e.line}", f"{e.name}=={e.version} is denylisted"))
    for rel in UV_LOCKS:
        path = repo / rel
        if path.exists():
            for pkg in tomllib.loads(path.read_text(encoding="utf-8")).get("package", []):
                if pkg.get("version") in py.get(canonicalize_name(pkg["name"]), ()):
                    findings.append(Finding("DENYLIST", str(rel), f"{pkg['name']}=={pkg['version']} is denylisted"))
    for rel in (PY_DIR / "pyproject.toml", *(lock.parent / "pyproject.toml" for lock in UV_LOCKS)):
        if not (repo / rel).exists():
            continue
        project = tomllib.loads((repo / rel).read_text(encoding="utf-8")).get("project", {})
        reqs = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            reqs += extra
        for spec in reqs:
            req = Requirement(spec)
            for bad in sorted(py.get(canonicalize_name(req.name), ())):
                if not req.url and req.specifier.contains(Version(bad), prereleases=True):
                    findings.append(Finding("DENYLIST", str(rel), f"`{spec}` admits denylisted {req.name} {bad}; bound it out"))
    npm = deny["npm"]
    for rel, data in _node_locks(repo):
        for key, pkg in data.get("packages", {}).items():
            name = pkg.get("name") or key.rpartition("node_modules/")[2]
            if pkg.get("version") in npm.get(name, ()):
                findings.append(Finding("DENYLIST", rel, f"{name}@{pkg['version']} is denylisted"))
    return findings


# --- node -----------------------------------------------------------------------------------------------------


def _walk(repo: Path, names: tuple[str, ...] = (), pattern: re.Pattern[str] | None = None,
          skip_top: tuple[str, ...] = ()) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and str(rel_root / d) not in skip_top)
        for f in sorted(files):
            if f in names or (pattern is not None and pattern.search(f)):
                out.append(Path(root) / f)
    return out


def _node_locks(repo: Path) -> list[tuple[str, dict]]:
    return [(str(p.relative_to(repo)), json.loads(p.read_text(encoding="utf-8")))
            for p in _walk(repo, names=("package-lock.json",))]


# npm-package-arg reads a file: target as a URL relative to the lock: a leading ~ is the home directory, and
# `.`/`..` segments, percent escapes, backslashes, `?`, `#` and drive letters change the path it opens. A target of
# plain segments, none starting with a dot, means the same path to npm and to this check.
_VENDORED_TARGET = re.compile(r"[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*(?:/[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*)*")


def _vendored_tarball_problem(repo: Path, lock_dir: Path, target: str, integrity: str) -> str | None:
    """Why a `file:` lock entry is not a tarball vendored inside the repository whose bytes match its sha512, or None."""
    path = repo / lock_dir / target
    if not _VENDORED_TARGET.fullmatch(target) or not path.resolve().is_relative_to(repo.resolve()):
        return f"file:{target} is not a path inside the repository"
    if not path.is_file():
        return f"file:{target} is not a vendored tarball in the repository"
    digest = "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()
    # npm accepts the file if it matches any listed hash, so only the file's own hash may be listed.
    if integrity != digest:
        return f"file:{target}: the lock's integrity is not exactly the sha512 of the file"
    return None


def check_node_locks(repo: Path) -> list[Finding]:
    findings = []
    locked_dirs = set()
    for rel, data in _node_locks(repo):
        locked_dirs.add(str(Path(rel).parent))
        if data.get("lockfileVersion", 1) < 2:
            findings.append(Finding("NODE-INTEGRITY", rel, "lockfileVersion 1 has no per-package integrity"))
        for key, pkg in data.get("packages", {}).items():
            if not key or pkg.get("link") or pkg.get("inBundle"):
                continue
            resolved, integrity = pkg.get("resolved", ""), pkg.get("integrity", "")
            if not integrity.startswith("sha512-"):
                findings.append(Finding("NODE-INTEGRITY", rel, f"{key} has no sha512 integrity"))
            elif resolved.startswith("file:"):
                if why := _vendored_tarball_problem(repo, Path(rel).parent, resolved.removeprefix("file:"), integrity):
                    findings.append(Finding("NODE-INTEGRITY", rel, f"{key}: {why}"))
            elif not resolved.startswith(NPM_HOSTS):
                findings.append(Finding("NODE-INTEGRITY", rel, f"{key} resolves outside the npm registry: {resolved}"))
    for p in _walk(repo, names=("package.json",)):
        rel_dir = str(p.parent.relative_to(repo))
        data = json.loads(p.read_text(encoding="utf-8"))
        has_deps = any(data.get(k) for k in ("dependencies", "devDependencies", "optionalDependencies"))
        if has_deps and rel_dir not in locked_dirs:
            findings.append(Finding("NODE-INTEGRITY", str(p.relative_to(repo)), "declares dependencies but has no package-lock.json"))
    return findings


# --- install commands -----------------------------------------------------------------------------------------

_NPM_INSTALL = re.compile(r"\b(?:npm\s+(?:install|i|add)|yarn\s+(?:install|add)|pnpm\s+(?:install|i|add))(?=\s|$)|^\s*yarn\s*$")
_PIP_INSTALL = re.compile(r"\b(?:pip3?|python3?(?:\.\d+)?\s+-m\s+pip|uv\s+pip)\s+install\b")
_UV_SYNC = re.compile(r"\buv\s+sync\b")
# Each resolves a throwaway environment from the index at run time: no lock, no hash, whatever was published last.
_PY_ADHOC = re.compile(r"\buv\s+run\b.*\s--with(?:=|\s)|\buvx\s|\buv\s+tool\s+(?:install|run)\b|\bpipx\s+(?:install|run)\b")
_NPX = re.compile(r"\bnpx\s+(?P<rest>.*)")
# A shell script's or a Makefile's quoted text is a message (`echo "pip install x"`), not a command it runs.
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'')
_SCRIPT_FILE = re.compile(r"\.(?:sh|mk)$|^Makefile$")


def _install_script_files(repo: Path) -> list[Path]:
    """Where an install runs beside something worth protecting: an image build, and a CI job (its secrets and
    token are in the environment a lifecycle script inherits; C644-FU-2)."""
    files = _walk(repo, pattern=re.compile(r"^Dockerfile"), skip_top=("docs",))
    return files + sorted((repo / ".github" / "workflows").glob("*.y*ml"))


def _install_files(repo: Path) -> list[Path]:
    """Every file that runs or tells someone to run an install: image builds, CI, the shell scripts and Makefiles
    a contributor or a job runs, and the contributor guide with its translations (whose setup a reader copies)."""
    files = [*_install_script_files(repo), repo / "CONTRIBUTING.md",
             *sorted((repo / "docs" / "i18n").glob("*/CONTRIBUTING.md")),
             *_walk(repo, pattern=_SCRIPT_FILE, skip_top=("docs",))]
    return [f for f in files if f.exists()]


def _command_lines(path: Path, keep_quotes: bool = False) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if path.suffix == ".md":
        keep, inside = [], False
        for n, line in enumerate(lines, 1):
            if line.lstrip().startswith("```"):
                inside = not inside
                continue
            if inside:
                keep.append((n, line))
    elif _SCRIPT_FILE.search(path.name) and not keep_quotes:
        keep = [(n, _QUOTED.sub('""', line)) for n, line in enumerate(lines, 1)]
    else:
        keep = list(enumerate(lines, 1))
    out, buf, start = [], "", 0
    for n, line in keep:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not buf:
            start = n
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        out.append((start, buf))
        buf = ""
    return out


def _locked_node(repo: Path) -> tuple[set[str], set[str]]:
    """(the commands, the package names) that some committed package-lock.json installs."""
    bins, names = set(), set()
    for _, data in _node_locks(repo):
        for key, pkg in data.get("packages", {}).items():
            if not key:
                continue
            name = key.rsplit("node_modules/", 1)[-1]
            names.add(name)
            declared = pkg.get("bin")
            bins |= set(declared) if isinstance(declared, dict) else {name.rsplit("/", 1)[-1]} if declared else set()
    return bins, names


def _npx_problem(rest: str, bins: set[str], names: set[str]) -> str | None:
    """Why `npx REST` would fetch from the registry, or None when a committed lock installs what it runs."""
    words = rest.split()
    while words and words[0].startswith("-"):
        flag = words.pop(0)
        if flag in ("-p", "--package"):
            flag = f"{flag}={words.pop(0) if words else ''}"
        if flag.startswith(("-p=", "--package=")) and (package := flag.split("=", 1)[1]) not in names:
            return f"--package {package} is not a package a committed lock installs"
    if words and words[0] not in bins:
        return f"{words[0]} is not a command a committed package-lock.json installs"
    return None


def _package_scripts(repo: Path) -> list[tuple[str, int, str]]:
    out = []
    for path in _walk(repo, names=("package.json",)):
        rel = str(path.relative_to(repo))
        text = path.read_text(encoding="utf-8")
        for name, script in (json.loads(text).get("scripts") or {}).items():
            line = next((n for n, ln in enumerate(text.splitlines(), 1) if f'"{name}"' in ln), 0)
            out.append((rel, line, script))
    return out


def _contract_list(repo: Path, contract: Path, key: str) -> list:
    """``key`` of the shared contract, then of the build's own file of the same name under EXTRA_CONTRACTS."""
    items: list = []
    for path in (repo / contract, repo / EXTRA_CONTRACTS / contract.name):
        if path.exists():
            items += json.loads(path.read_text(encoding="utf-8")).get(key, [])
    return items


def _unlocked_installs(repo: Path) -> list[dict]:
    return _contract_list(repo, UNLOCKED_INSTALLS, "installs")


def check_install_commands(repo: Path) -> list[Finding]:
    found: list[tuple[Finding, str, str]] = []
    bins, names = _locked_node(repo)

    def scan(rel: str, n: int, code: str) -> None:
        for cmd in re.split(r"&&|\|\||;|\|", code):
            text = cmd.strip()[:100]
            if _NPM_INSTALL.search(cmd):
                found.append((Finding("NODE-CI", f"{rel}:{n}", f"use `npm ci` against a committed package-lock.json: {text}"), rel, cmd))
            if _PIP_INSTALL.search(cmd) and "--require-hashes" not in cmd:
                found.append((Finding("PY-INSTALL", f"{rel}:{n}",
                                      "install from a hashed lock (backend/python/requirements/install.sh, "
                                      f"or --require-hashes): {text}"), rel, cmd))
            if _UV_SYNC.search(cmd) and not re.search(r"--(?:locked|frozen)\b", cmd):
                found.append((Finding("PY-INSTALL", f"{rel}:{n}", f"`uv sync` must be --locked: {text}"), rel, cmd))
            if _PY_ADHOC.search(cmd):
                found.append((Finding("PY-INSTALL", f"{rel}:{n}",
                                      f"an environment resolved at run time, outside every lock: {text}"), rel, cmd))
            if (m := _NPX.search(cmd)) and (why := _npx_problem(m.group("rest"), bins, names)):
                found.append((Finding("NODE-NPX", f"{rel}:{n}", f"npx would fetch from the registry: {why}"), rel, cmd))

    for path in _install_files(repo):
        rel = str(path.relative_to(repo))
        for n, line in _command_lines(path):
            scan(rel, n, re.sub(r"\s#\s.*$", "", line))
    for rel, n, script in _package_scripts(repo):
        scan(rel, n, script)
    # SEC-SC-07: what may stay unlocked is listed with its reason; an entry that excuses nothing leaves the list.
    excused = _unlocked_installs(repo)
    used: set[int] = set()
    findings = []
    for finding, rel, cmd in found:
        hit = next((i for i, e in enumerate(excused)
                    if e["rule"] == finding.rule and e["file"] == rel and e["match"] in cmd), None)
        if hit is None:
            findings.append(finding)
        else:
            used.add(hit)
    findings += [Finding(e["rule"], str(UNLOCKED_INSTALLS), f"{e['file']}: nothing runs `{e['match']}` now; drop the entry")
                 for i, e in enumerate(excused) if i not in used]
    return findings


_NPM_CI = re.compile(r"\bnpm\s+ci\b")
_NPM_REBUILD = re.compile(r"\bnpm\s+rebuild\b(?P<names>[^&|;]*)")


def check_install_scripts(repo: Path) -> list[Finding]:
    """SEC-SC-08: an image build or a CI job runs no npm install script but those named in install-scripts.json."""
    path = repo / INSTALL_SCRIPTS
    contract = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"packages": {}, "scriptedInstalls": []}
    findings = []
    for rel, data in _node_locks(repo):
        scripted = {key.rsplit("node_modules/", 1)[-1] for key, pkg in data.get("packages", {}).items()
                    if key and pkg.get("hasInstallScript")}
        listed = set(contract["packages"].get(rel, {}))
        for name in sorted(scripted - listed):
            findings.append(Finding("NODE-SCRIPTS", rel, f"{name} has an install script; list it in {INSTALL_SCRIPTS}"))
        for name in sorted(listed - scripted):
            findings.append(Finding("NODE-SCRIPTS", str(INSTALL_SCRIPTS), f"{rel}: {name} has no install script now; drop it"))
    rebuild = {name for pkgs in contract["packages"].values() for name, how in pkgs.items() if how["run"] == "rebuild"}
    allowed = {(item["file"], item["command"]) for item in contract["scriptedInstalls"]}
    for path in _install_script_files(repo):
        rel = str(path.relative_to(repo))
        for n, line in _command_lines(path):
            for part in re.split(r"&&|\|\||;|\|", re.sub(r"\s#\s.*$", "", line)):
                cmd = re.sub(r"^(?:RUN\s+(?:--\S+\s+)*|-?\s*run:\s*)", "", part.strip()).lstrip("(").rstrip(")").strip()
                if _NPM_CI.search(cmd) and "--ignore-scripts" not in cmd and (rel, cmd) not in allowed:
                    findings.append(Finding("NODE-SCRIPTS", f"{rel}:{n}",
                                            f"`npm ci` runs every install script in the lock; add --ignore-scripts: {cmd[:100]}"))
                if m := _NPM_REBUILD.search(cmd):
                    for name in m.group("names").split():
                        if not name.startswith("-") and name not in rebuild:
                            findings.append(Finding("NODE-SCRIPTS", f"{rel}:{n}",
                                                    f"npm rebuild {name}: not a `rebuild` entry of {INSTALL_SCRIPTS}"))
    return findings


# --- images ---------------------------------------------------------------------------------------------------

_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(?P<ref>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.I)
# An image a build copies files out of is code entering the build as much as a FROM is.
_COPY_FROM = re.compile(r"^\s*(?:COPY|ADD)\b.*?--from=(?P<ref>\S+)", re.I)
_ARG = re.compile(r"^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>\S+)")
_IMAGE = re.compile(r"^\s*image:\s*['\"]?(?P<ref>[^'\"#\s]+)")
_VAR = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-?+])?(?P<default>[^}]*)\}|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)")


def _expand(ref: str, args: dict[str, str]) -> str | None:
    """A reference with its variables replaced by their defaults, or None if one has no default."""
    missing = False

    def sub(m: re.Match[str]) -> str:
        nonlocal missing
        name = m.group("name") or m.group("bare")
        if name in args:
            return args[name]
        if m.group("op") and m.group("op").endswith("-"):
            return _expand(m.group("default"), args) or ""
        missing = True
        return ""

    for _ in range(4):
        new = _VAR.sub(sub, ref)
        if new == ref:
            break
        ref = new
    return None if missing or not ref else ref


def image_refs(repo: Path) -> dict[str, set[str]]:
    """Every third-party or first-party image a Dockerfile builds from or a compose file runs, by reference."""
    refs: dict[str, set[str]] = {}
    for path in _walk(repo, pattern=re.compile(r"^Dockerfile"), skip_top=("docs",)):
        rel = str(path.relative_to(repo))
        args: dict[str, str] = {}
        stages: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if m := _ARG.match(line):
                args[m.group("name")] = m.group("value").strip("'\"")
            elif m := _FROM.match(line):
                ref = _expand(m.group("ref"), args)
                if ref and ref.lower() != "scratch" and ref not in stages:
                    refs.setdefault(ref, set()).add(rel)
                if m.group("stage"):
                    stages.add(m.group("stage"))
            elif m := _COPY_FROM.match(line):
                ref = _expand(m.group("ref"), args)
                if ref and ref not in stages and not ref.isdigit():
                    refs.setdefault(ref, set()).add(rel)
    # A CI job's service and container images run beside the job's secrets.
    workflows = sorted((repo / ".github" / "workflows").glob("*.y*ml"))
    for path in _walk(repo, pattern=re.compile(r"compose[^/]*\.ya?ml$"), skip_top=("docs",)) + workflows:
        rel = str(path.relative_to(repo))
        for line in path.read_text(encoding="utf-8").splitlines():
            if m := _IMAGE.match(line):
                ref = _expand(m.group("ref"), {})
                if ref:
                    refs.setdefault(ref, set()).add(rel)
    return refs


# An exact release: 1.2.3 (optionally -suffix, e.g. 7.4.12-bookworm, 1.27.5-alpine) or a dated build
# (2026.9.30-a9d990033). "v1.15", "7.4-bookworm" and "latest" move when upstream publishes.
_EXACT_TAG = re.compile(r":v?\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?$|:\d{4}\.\d+\.\d+-[0-9a-f]+$")


def check_images(repo: Path) -> list[Finding]:
    unpinned = {ref: files for ref, files in image_refs(repo).items() if "@sha256:" not in ref}
    items = _contract_list(repo, IMAGE_PINS, "tagOnly")
    listed = {item["ref"]: set(item["files"]) for item in items}
    findings = []
    # C6.44 (the file's $comment): a third-party image is pinned by digest, except a store
    # in the files install.sh runs, which an air-gapped host loads by name; first-party images wait for signing.
    user_run = set(_contract_list(repo, IMAGE_PINS, "userRunFiles"))
    for item in items:
        category = item.get("category")
        if category == "user-run-service" and not _EXACT_TAG.search(item["ref"]):
            findings.append(Finding("IMAGE-PIN", str(IMAGE_PINS),
                                    f"{item['ref']} is a floating tag; a user-run service keeps an exact version tag"))
        if category == "user-run-service" and not set(item["files"]) <= user_run:
            findings.append(Finding("IMAGE-PIN", str(IMAGE_PINS),
                                    f"{item['ref']} is used outside the files install.sh runs: pin it by digest there"))
        elif category not in ("first-party", "user-run-service"):
            findings.append(Finding("IMAGE-PIN", str(IMAGE_PINS),
                                    f"{item['ref']} is third-party: pin it by digest rather than list it"))
    for ref, files in sorted(unpinned.items()):
        if ref not in listed:
            findings.append(Finding("IMAGE-PIN", ", ".join(sorted(files)),
                                    f"{ref} is pinned by tag only; pin it by digest (or list it in {IMAGE_PINS})"))
        elif listed[ref] != files:
            findings.append(Finding("IMAGE-PIN", str(IMAGE_PINS),
                                    f"{ref}: the files listed differ from where it is used ({', '.join(sorted(files))})"))
    for ref in sorted(set(listed) - set(unpinned)):
        findings.append(Finding("IMAGE-PIN", str(IMAGE_PINS), f"{ref} is no longer used by tag only; drop the entry"))
    return findings


def image_report(repo: Path) -> dict:
    items = []
    for ref, files in sorted(image_refs(repo).items()):
        if "@sha256:" in ref:
            continue
        digest = None
        if shutil.which("docker"):
            r = subprocess.run(["docker", "image", "inspect", ref, "--format", "{{json .RepoDigests}}"],
                               capture_output=True, text=True, check=False)
            if r.returncode == 0:
                digests = json.loads(r.stdout or "[]")
                digest = digests[0].split("@", 1)[1] if digests else None
        items.append({"ref": ref, "files": sorted(files), "localDigest": digest})
    return {"tagOnly": items}


# --- lock generation (needs uv and the network) ---------------------------------------------------------------


def _header(name: str, inputs: str, cutoff: str) -> str:
    if spec := STANDALONE_LOCKS.get(name):
        install = spec.install
        where = "Linux (x86_64, aarch64)" if spec.linux_only else "Linux (x86_64, aarch64) and Apple-silicon macOS"
    else:
        install = f"backend/python/requirements/install.sh {name} (pip --require-hashes --no-deps)"
        where = "Linux (x86_64, aarch64; torch from the CPU index) and Apple-silicon macOS"
    return (
        f"# PipesHub Python lock: {name}. Generated by `python3 scripts/supply_chain.py lock`; do not edit by hand.\n"
        f"# Install with {install}.\n"
        f"# CPython 3.12 on {where}.\n"
        f"# inputs-sha256: {inputs}\n"
        f"# exclude-newer: {cutoff}\n\n"
    )


def _split_vcs(text: str) -> tuple[str, list[str]]:
    """uv's output without its git requirements (pip cannot hash a checkout), and those requirements."""
    kept, vcs, skipping = [], [], False
    for line in text.splitlines():
        if re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*@\s*git\+", line):
            vcs.append(line.rstrip(" \\"))
            skipping = True
            continue
        if skipping and line.startswith("    "):
            continue
        skipping = False
        kept.append(line)
    return "\n".join(kept).rstrip() + "\n", vcs


def generate_locks(repo: Path, out_dir: Path, uv: str, cutoff: str, constraints: list[str],
                   upgrade: tuple[str, ...] = ()) -> None:
    """Resolve every lock into out_dir/requirements, reusing the locks already there as preferences."""
    inputs = fingerprint(repo)
    vcs: list[str] = []
    # [tool.uv] in pyproject.toml configures the resolve; UV_* settings (an index mirror, say) do not, and the
    # index flags in UV_FLAGS win over a user uv.toml.
    env = {k: v for k, v in os.environ.items() if not k.startswith("UV_") or k == "UV_CACHE_DIR"}
    for name in HASHED_LOCKS:
        target = out_dir / "requirements" / f"{name}.lock"
        cmd = [uv, "pip", "compile", *LOCK_INPUTS[name], *UV_FLAGS, "--exclude-newer", cutoff,
               "-o", f"requirements/{name}.lock"]
        for c in constraints:
            cmd += ["-c", c]
        for package in upgrade:
            cmd += ["--upgrade-package", package]
        subprocess.run(cmd, cwd=out_dir, check=True, env=env)
        body, found = _split_vcs(target.read_text(encoding="utf-8"))
        vcs += [v for v in found if v not in vcs]
        target.write_text(_header(name, inputs, cutoff) + body, encoding="utf-8")
    (out_dir / "requirements" / "vcs.lock").write_text(
        "# Source requirements pip cannot hash, each pinned to a full git commit id and installed with --no-deps\n"
        "# after the hashed lock (their dependencies are in it). Generated by `python3 scripts/supply_chain.py lock`.\n"
        + "".join(v + "\n" for v in sorted(vcs)),
        encoding="utf-8",
    )


def generate_standalone_lock(repo: Path, name: str, out_dir: Path, uv: str, cutoff: str, constraints: list[str],
                             upgrade: tuple[str, ...] = ()) -> None:
    """A standalone lock, resolved in a throwaway project that names only its target platforms."""
    spec = STANDALONE_LOCKS[name]
    (out_dir / "pyproject.toml").write_text(
        f'[project]\nname = "pxh-{name}-lock"\nversion = "0"\nrequires-python = ">=3.12"\n\n'
        f"[tool.uv]\nenvironments = {json.dumps(_environments(linux_only=spec.linux_only))}\n", encoding="utf-8")
    shutil.copy2(repo / spec.source, out_dir / "requirements.in")
    if (repo / spec.lock).exists():
        shutil.copy2(repo / spec.lock, out_dir / "requirements.lock")
    env = {k: v for k, v in os.environ.items() if not k.startswith("UV_") or k == "UV_CACHE_DIR"}
    cmd = [uv, "pip", "compile", "requirements.in", *UV_FLAGS, "--exclude-newer", cutoff, "-o", "requirements.lock"]
    for c in constraints:
        cmd += ["-c", c]
    for package in upgrade:
        cmd += ["--upgrade-package", package]
    subprocess.run(cmd, cwd=out_dir, check=True, env=env)
    target = out_dir / "requirements.lock"
    target.write_text(_header(name, standalone_fingerprint(repo, name), cutoff) + target.read_text(encoding="utf-8"),
                      encoding="utf-8")


def _stage(repo: Path, tmp: Path) -> None:
    (tmp / "requirements").mkdir(parents=True)
    shutil.copy2(repo / PY_DIR / "pyproject.toml", tmp / "pyproject.toml")
    for f in (repo / REQ_DIR).iterdir():
        if f.suffix in (".in", ".lock"):
            shutil.copy2(f, tmp / "requirements" / f.name)


LOCK_SETS = ("backend", *STANDALONE_LOCKS)


def _recorded_cutoff(repo: Path, lock: Path) -> str | None:
    return parse_lock(repo / lock, "").header.get("exclude-newer") if (repo / lock).exists() else None


def cmd_lock(repo: Path, check: bool, constraints: list[str], sets: tuple[str, ...] = LOCK_SETS,
             upgrade: tuple[str, ...] = ()) -> int:
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        print("uv not found: install it from backend/python/requirements/tools.lock (install.sh tools)", file=sys.stderr)
        return 2
    when = _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=COOLDOWN_DAYS)
    fresh = when.strftime("%Y-%m-%dT00:00:00Z")

    # A lock set may be regenerated alone (--set), so each records its own cutoff and --check resolves against it.
    def cutoff(lock: Path) -> str:
        return (_recorded_cutoff(repo, lock) if check else None) or fresh

    constraints = [str(Path(c).resolve()) for c in constraints]
    with tempfile.TemporaryDirectory(prefix="pxh-lock-") as d:
        tmp = Path(d)
        outputs: list[tuple[Path, Path]] = []
        if "backend" in sets:
            _stage(repo, tmp / "backend")
            generate_locks(repo, tmp / "backend", uv, cutoff(REQ_DIR / "dev.lock"), constraints, upgrade)
            outputs += [(tmp / "backend" / "requirements" / f"{n}.lock", repo / REQ_DIR / f"{n}.lock")
                        for n in (*HASHED_LOCKS, "vcs")]
        for name, spec in STANDALONE_LOCKS.items():
            if name not in sets or not (repo / spec.source).exists():
                continue
            (tmp / name).mkdir()
            generate_standalone_lock(repo, name, tmp / name, uv, cutoff(spec.lock), constraints, upgrade)
            outputs.append((tmp / name / "requirements.lock", repo / spec.lock))
        if check:
            stale = [dst for src, dst in outputs if not dst.exists() or src.read_text() != dst.read_text()]
            for dst in stale:
                print(f"PY-LOCK-STALE: {dst.relative_to(repo)}: a fresh resolve writes a different file", file=sys.stderr)
            return 1 if stale else 0
        for src, dst in outputs:
            shutil.copy2(src, dst)
    return 0


# --- hash verification against the index (online) -------------------------------------------------------------


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "pipeshub-supply-chain-check"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - fixed https hosts
        return r.read()


def published_files(name: str, version: str, cache: dict) -> dict[str, str]:
    """sha256 -> file name for one release, from PyPI's JSON API or the PyTorch CPU index's simple page."""
    key = canonicalize_name(name)
    if key in TORCH_PACKAGES:
        if key not in cache:
            html = _fetch(f"{TORCH_CPU}/{key}/").decode()
            cache[key] = {m.group(2): m.group(1).rsplit("/", 1)[-1]
                          for m in re.finditer(r'href="([^"#]+)#sha256=([0-9a-f]{64})"', html)}
        files = {h: f.replace("%2B", "+") for h, f in cache[key].items()}
        return {h: f for h, f in files.items() if f.split("-")[1] == version}
    data = json.loads(_fetch(f"https://pypi.org/pypi/{name}/{version}/json"))
    if any(u.get("yanked") for u in data["urls"]):
        return {h: "YANKED" for u in data["urls"] for h in [u["digests"]["sha256"]]}
    return {u["digests"]["sha256"]: u["filename"] for u in data["urls"]}


def _wheel_fits(filename: str, env: dict[str, str]) -> bool:
    if not filename.endswith(".whl"):
        return False
    py, abi, plat = filename[:-4].split("-")[-3:]
    py_ok = any(t in ("py3", "cp312") or (t.startswith("py3")) or (abi == "abi3" and t.startswith("cp3") and int(t[3:]) <= 12)
                for t in py.split("."))
    if not py_ok:
        return False
    plats = plat.split(".")
    if "any" in plats:
        return True
    machine = env["platform_machine"]
    if env["sys_platform"] == "linux":
        return any(("manylinux" in p or p.startswith("linux")) and p.endswith(machine) for p in plats)
    return any(p.startswith("macosx") and (p.endswith(machine) or p.endswith("universal2")) for p in plats)


def cmd_verify_hashes(repo: Path) -> int:
    locks, findings = load_locks(repo)
    cache: dict = {}
    sdist_builds: set[str] = set()
    checked: dict[tuple[str, str], dict[str, str]] = {}
    for lock in locks.values():
        for e in lock.entries:
            k = (e.key, e.version)
            if k not in checked:
                checked[k] = published_files(e.name, e.version, cache)
            files = checked[k]
            if "YANKED" in files.values():
                findings.append(Finding("PY-HASH", f"{lock.path}:{e.line}", f"{e.name}=={e.version} is yanked"))
            unknown = [h for h in e.hashes if h not in files]
            if unknown:
                findings.append(Finding("PY-HASH", f"{lock.path}:{e.line}",
                                        f"{e.name}=={e.version}: {len(unknown)} hash(es) the index does not publish"))
            mine = [files[h] for h in e.hashes if h in files]
            for env in TARGET_ENVIRONMENTS:
                if not _marker_applies(e.marker, env) or any(_wheel_fits(f, env) for f in mine):
                    continue
                if any(f.endswith((".tar.gz", ".zip")) for f in mine):
                    sdist_builds.add(f"{e.name}=={e.version} ({env['sys_platform']}/{env['platform_machine']})")
                else:
                    findings.append(Finding("PY-HASH", f"{lock.path}:{e.line}",
                                            f"{e.name}=={e.version} has no file for {env['sys_platform']}/{env['platform_machine']}"))
    for f in findings:
        print(f, file=sys.stderr)
    print(f"verified {len(checked)} releases; built from source: {', '.join(sorted(sdist_builds)) or 'none'}")
    return 1 if findings else 0


# --- entry point ----------------------------------------------------------------------------------------------


def check_installed(repo: Path, site_packages: Path, lock_name: str = "runtime") -> list[Finding]:
    """Every pin of a lock that applies on Linux x86_64 against what a site-packages directory holds."""
    import importlib.metadata as md

    installed: dict[str, str] = {}
    for dist in md.distributions(path=[str(site_packages)]):
        name = dist.metadata["Name"]
        if name:
            installed.setdefault(canonicalize_name(name), dist.version)
    rel = str(REQ_DIR / f"{lock_name}.lock")
    linux = TARGET_ENVIRONMENTS[0]
    findings: list[Finding] = []
    for entry in parse_lock(repo / rel, rel).entries:
        if not _marker_applies(entry.marker, linux):
            continue
        have = installed.get(entry.key)
        if have is None:
            findings.append(Finding("PY-INSTALLED", str(site_packages), f"{entry.name}=={entry.version} is not installed"))
        elif Version(_norm_version(have)) != Version(_norm_version(entry.version)):
            findings.append(Finding("PY-INSTALLED", str(site_packages),
                                    f"{entry.name} is {have}, {rel} pins {entry.version}"))
    return findings


# --- Helm images, fetched code and GitHub Actions (SEC-D-11) -------------------------------------------------

HELM_DIR = Path("deployment/helm")
#: Images PipesHub builds itself: signed at release and pinned by the digest the release publishes (SEC-D-04).
FIRST_PARTY_REPOS = ("pipeshubai/", "pipeshub/")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_YAML_KV = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z][A-Za-z0-9_]*):\s*(?P<value>.*?)\s*(?:#.*)?$")


def _unquote(value: str) -> str:
    return value.strip().strip("'\"")


def helm_images(repo: Path) -> list[tuple[str, int, str, str]]:
    """(file, line, reference, digest) for every image a chart's values name: a repository/tag/digest block, or
    an `image:` string. Line-based (standard library only); values files keep one key per line."""
    out = []
    for path in _walk(repo / HELM_DIR, pattern=re.compile(r"^values[^/]*\.ya?ml$")):
        rel = str(path.relative_to(repo))
        lines = path.read_text(encoding="utf-8").splitlines()
        for n, line in enumerate(lines, 1):
            m = _YAML_KV.match(line)
            if not m:
                continue
            key, value = m.group("key"), _unquote(m.group("value"))
            if key == "image" and value and value not in ("{}",):
                out.append((rel, n, value, value.split("@", 1)[1] if "@" in value else ""))
            elif key == "repository" and value:
                block = {"repository": value}
                indent = m.group("indent")
                for other in lines[max(0, n - 4):n + 4]:
                    o = _YAML_KV.match(other)
                    if o and o.group("indent") == indent and o.group("key") in ("registry", "tag", "digest"):
                        block[o.group("key")] = _unquote(o.group("value"))
                ref = block["repository"] + (f":{block['tag']}" if block.get("tag") else "")
                out.append((rel, n, ref, block.get("digest", "")))
    return out


def check_helm_images(repo: Path) -> list[Finding]:
    findings = []
    for rel, n, ref, digest in helm_images(repo):
        name = ref.split("@", 1)[0]
        if name.startswith(FIRST_PARTY_REPOS):
            # A moving release tag on a chart's own image; values-local.yaml is the developer's kind cluster.
            if name.rsplit(":", 1)[-1] in ("latest", "slim") and ":" in name and not rel.endswith("values-local.yaml"):
                findings.append(Finding("IMAGE-PIN", f"{rel}:{n}", f"{name} moves with every release; leave the tag "
                                        "empty (the chart's appVersion) and pin image.digest"))
            continue
        if not _DIGEST.match(digest):
            findings.append(Finding("IMAGE-PIN", f"{rel}:{n}",
                                    f"{name} is a third-party image the chart runs by tag; give it its sha256 digest"))
    return findings


# A remote script run by a shell or interpreter the moment it arrives: nothing checks what was fetched.
# `| python3 -c '...'` reads the response as data (the program is the -c text), so it is not flagged.
# An env assignment's name holds no `=`: with `\S+=\S+` a line like `| env a==b env a==b ...` splits two ways per
# assignment and the scan backtracks exponentially.
_PIPE_SHELL = re.compile(
    r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:env\s+[^\s=]+=\S+\s+)*"
    r"(?:/usr/(?:local/)?bin/|/bin/)?(?:(?:ba|z|da|k)?sh|python\d*(?:\.\d+)?|perl|node|ruby|php)\b(?!\s+-c\b)"
)
# A fetch that keeps what it fetched (-o/-O/--output, a redirect, or a pipe into an unpacker); a POST or an
# upload sends rather than fetches.
_DOWNLOAD = re.compile(r"\b(?:curl|wget)\b(?=.*(?:\s-[A-Za-z]*[oO]\b|\s--output\b|\s-O\s|>\s*\S|\|\s*(?:sudo\s+)?(?:tar|unzip|gunzip|bsdtar)\b))")
_SENDS = re.compile(r"\s(?:-X\s*(?:POST|PUT|PATCH)|-F|--form|-d|--data\S*|-T|--upload-file)\b")
# Something executable or unpacked into the build: an archive, a package, an installer or a binary.
_FETCHED_CODE = re.compile(
    r"\.(?:zip|tar|tgz|tar\.gz|tar\.xz|txz|tar\.bz2|tbz2?|tar\.zst|deb|rpm|apk|whl|AppImage|exe|msi|pkg|dmg|run|bin|sh)\b"
    r"|\brustup-init\b|/usr/local/bin/|/usr/bin/|\|\s*(?:sudo\s+)?(?:tar|unzip|gunzip|bsdtar)\b"
)
_VERIFIES = re.compile(
    r"\b(?:sha256sum|sha512sum)\b[^|;&]*\s-c\b|\bshasum\s+-a\s+(?:256|512)\b[^|;&]*\s-c\b"
    r"|\bcosign\s+verify-blob\b|\bgpg\s+--verify\b|\bminisign\s+-V\b|--sha256\s"
)
#: How many later commands in the same file may hold the check of a download.
DOWNLOAD_CHECK_WINDOW = 25


def _fetch_files(repo: Path) -> list[Path]:
    """Where fetched code would run beside something worth protecting: image builds, CI jobs and their actions."""
    actions = _walk(repo / ".github" / "actions", pattern=re.compile(r"^action\.ya?ml$"))
    return _install_script_files(repo) + actions


# Operator scripts (R41SUPPLY-FU-10). Where a fetch saves its file; the file counts as code when a later command
# installs, unpacks, marks executable or runs it.
_SAVED_TO = re.compile(r"\s(?:-[A-Za-z]*o|--output|-[A-Za-z]*O)(?:=|\s+)(?P<target>\"[^\"]+\"|'[^']+'|[^\s;&|]+)")
_USES_AS_CODE = re.compile(
    r"\binstall\s|\bchmod\s+(?:\+x|[0-7]*[157]{2,3})\b|\bmv\b.*/bin\b|\btar\s|\bunzip\b|\binstaller\s+-pkg\b"
    r"|\b(?:ba|z)?sh\s|\bexec\s|\bdocker\s+load\b"
)
_SHELL_FUNCTION = re.compile(r"^\s*(?:function\s+)?(?P<name>[A-Za-z_][\w-]*)\s*\(\)\s*\{")
# A checksum read from the server the download comes from proves the transfer, not the bytes.
_FETCHED_SUM = re.compile(r"\$\(\s*(?:curl|wget|fetch)\b")
_SUM_FILE = re.compile(r"(?:\.sha(?:256|512)(?:sum)?|\.asc|checksums?(?:\.txt)?|SHA(?:256|512)SUMS)[\"']?$", re.I)


def _verifier_functions(path: Path) -> set[str]:
    """Shell functions in ``path`` whose body runs a checksum or signature check: a call to one is that check."""
    names, current = set(), None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _SHELL_FUNCTION.match(line)
        if m:
            current = m.group("name")
            if "}" in line[m.end():] and _VERIFIES.search(line):
                names.add(current)
            continue
        if current is None:
            continue
        if _VERIFIES.search(line):
            names.add(current)
        if line.startswith("}"):
            current = None
    return names


def _target(match: re.Match[str]) -> str:
    return match.group("target").strip("'\"")


def check_operator_downloads(path: Path, rel: str) -> list[Finding]:
    """A shell script an operator or a contributor runs installs only code it checked against a checksum it carries
    (DOWNLOAD-HASH for scripts, R41SUPPLY-FU-10)."""
    findings = []
    lines = _command_lines(path, keep_quotes=True)
    verifiers = _verifier_functions(path)
    calls = re.compile(r"(?:^|[\s;&|(])(?:" + "|".join(map(re.escape, sorted(verifiers))) + r")\s") if verifiers else None
    for i, (n, text) in enumerate(lines):
        if not _DOWNLOAD.search(text) or _SENDS.search(text) or _SHELL_FUNCTION.match(text):
            continue
        saved = _SAVED_TO.search(text)
        target = _target(saved) if saved else ""
        if target in ("/dev/null", "-") or _SUM_FILE.search(target):
            continue
        window = [t for _, t in lines[i:i + DOWNLOAD_CHECK_WINDOW]]
        later = window[1:]
        code = bool(_FETCHED_CODE.search(text)) or bool(
            target and any(target in t and _USES_AS_CODE.search(t) for t in later))
        if not code:
            continue
        checks = [t for t in window if _VERIFIES.search(t) or (calls is not None and calls.search(t))]
        fetched_sums = [t for t in window if _DOWNLOAD.search(t)
                        and any(_SUM_FILE.search(_target(m)) for m in _SAVED_TO.finditer(t))]
        if not checks:
            findings.append(Finding("DOWNLOAD-HASH", f"{rel}:{n}",
                                    "a script installs downloaded code it never checks; check it with sha256sum -c "
                                    f"against a checksum the script carries: {text}"))
        elif fetched_sums or any(_FETCHED_SUM.search(t) for t in checks):
            findings.append(Finding("DOWNLOAD-HASH", f"{rel}:{n}",
                                    "the checksum comes from the server the download comes from, which proves the "
                                    f"transfer, not the bytes; pin it in the script: {text}"))
    return findings


def check_fetched_code(repo: Path) -> list[Finding]:
    findings = []
    shell_scripts = [p for p in _walk(repo, pattern=_SCRIPT_FILE, skip_top=("docs",))]
    for path in shell_scripts:
        findings.extend(check_operator_downloads(path, str(path.relative_to(repo))))
    for path in _fetch_files(repo) + shell_scripts:
        rel = str(path.relative_to(repo))
        lines = _command_lines(path)
        for i, (n, text) in enumerate(lines):
            if _PIPE_SHELL.search(text):
                findings.append(Finding("PIPE-SHELL", f"{rel}:{n}",
                                        "a fetched script runs unchecked; use a signed package repository, a "
                                        f"digest-pinned image or a pinned download checked with sha256sum -c: {text}"))
            elif (path not in shell_scripts and _DOWNLOAD.search(text) and not _SENDS.search(text)
                  and _FETCHED_CODE.search(text)):
                window = [t for _, t in lines[i:i + DOWNLOAD_CHECK_WINDOW]]
                if not any(_VERIFIES.search(t) for t in window):
                    findings.append(Finding("DOWNLOAD-HASH", f"{rel}:{n}",
                                            "downloaded code is never checked; follow it with sha256sum -c against "
                                            f"a pinned checksum: {text}"))
    return findings


_USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?(?P<ref>[^\s'\"#]+)")
_ACTION_SHA = re.compile(r"@[0-9a-f]{40}$")


def check_actions(repo: Path) -> list[Finding]:
    """Every action a workflow or composite action runs is pinned to a commit: a tag can be moved to other code."""
    findings = []
    files = sorted((repo / ".github" / "workflows").glob("*.y*ml"))
    files += _walk(repo / ".github" / "actions", pattern=re.compile(r"^action\.ya?ml$"))
    for path in files:
        rel = str(path.relative_to(repo))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _USES.match(line)
            if not m or m.group("ref").startswith("./"):
                continue
            ref = m.group("ref")
            if ref.startswith("docker://"):
                if "@sha256:" not in ref:
                    findings.append(Finding("ACTION-PIN", f"{rel}:{n}", f"{ref}: pin the image by digest"))
            elif not _ACTION_SHA.search(ref):
                findings.append(Finding("ACTION-PIN", f"{rel}:{n}",
                                        f"{ref}: pin the action to a 40-hex commit (keep the tag as a comment)"))
    return findings


def run_checks(repo: Path) -> list[Finding]:
    return (check_python_locks(repo) + check_denylist(repo) + check_node_locks(repo)
            + check_install_commands(repo) + check_install_scripts(repo) + check_images(repo)
            + check_helm_images(repo) + check_fetched_code(repo) + check_actions(repo))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    lock = sub.add_parser("lock")
    lock.add_argument("--check", action="store_true")
    lock.add_argument("--constraint", action="append", default=[],
                      help="extra constraints for a one-off seeding resolve (not recorded in the lock)")
    lock.add_argument("--set", action="append", choices=LOCK_SETS,
                      help="regenerate only these lock sets (default: all)")
    lock.add_argument("--upgrade-package", action="append", default=[], metavar="NAME",
                      help="let this package (a transitive one, say) move to its newest release before the cutoff")
    sub.add_parser("verify-hashes")
    sub.add_parser("images")
    installed = sub.add_parser("installed")
    installed.add_argument("site_packages", type=Path)
    installed.add_argument("--lock", default="runtime", choices=HASHED_LOCKS)
    args = parser.parse_args(argv)
    if args.cmd == "check":
        findings = run_checks(REPO)
        for f in findings:
            print(f, file=sys.stderr)
        print(f"supply-chain check: {len(findings)} finding(s)")
        return 1 if findings else 0
    if args.cmd == "lock":
        return cmd_lock(REPO, args.check, args.constraint, tuple(args.set or LOCK_SETS),
                        tuple(args.upgrade_package))
    if args.cmd == "verify-hashes":
        return cmd_verify_hashes(REPO)
    if args.cmd == "installed":
        findings = check_installed(REPO, args.site_packages, args.lock)
        for f in findings:
            print(f, file=sys.stderr)
        print(f"installed check: {len(findings)} difference(s) from {args.lock}.lock")
        return 1 if findings else 0
    print(json.dumps(image_report(REPO), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
