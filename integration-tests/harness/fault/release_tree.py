"""Release N as real code for the fault suite (QA-18): its Node and worker beside this tree's.

Release N is the ``gitRef`` of the newest replay corpus (``contracts/harness/vectors/replay-corpus/*/manifest.json``),
the release whose journals RU-3 already replays on this build. Its source is extracted once with ``git archive`` into
a cache directory outside the repository (no worktree, nothing written to the checkout) and run as the fault stack
runs this tree: its own ``fault-stack-server.ts`` under ts-node and its own ``worker.py``. It borrows this tree's
``node_modules``.

Its worker runs in a virtualenv of its own (``release_python``), layered on this tree's: the packages N declares and
this tree's virtualenv no longer has (python-jose before PyJWT, #4265; ``fastmcp`` before the MCP SDK 2), with what
they need that this tree's versions do not satisfy (the MCP SDK 1), installed at N's own pins from a hashed lock kept
beside the release; everything else is this tree's, found after them on ``sys.path``. A release before the locked
requirements (SEC-SC-07) has no lock of its own, so its lock is resolved once from its own ``pyproject.toml`` against
this tree's virtualenv and reused until either changes (WP88-FU-2).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import stack

CORPORA = stack.REPO / "contracts" / "harness" / "vectors" / "replay-corpus"
# Every file the release's Node and worker read at runtime, and nothing else.
PATHS = ("backend/nodejs/apps", "backend/python", "contracts", "integration-tests/harness/fault")


def release_ref() -> str:
    manifests = [json.loads(m.read_text()) for m in CORPORA.glob("*/manifest.json")]
    newest = max(manifests, key=lambda m: m["recordedAt"])
    return str(newest["gitRef"])


# Release trees recorded beside N; ``previous`` is the release before this build (N-1, FUPERF-FU-1).
RECORDED = Path(__file__).with_name("release-trees.json")


def previous_release() -> dict[str, object]:
    return dict(json.loads(RECORDED.read_text())["previous"])


def before(feature: str) -> str:
    """The last tree without ``feature`` (a key of ``previous.postdates``): the pre-feature rollback's target."""
    return str(dict(previous_release()["postdates"])[feature])  # type: ignore[call-overload]


def previous_ref() -> str:
    """The release before this build: where a rollback scenario rolls back to (R-29 promises N-1 only)."""
    return str(previous_release()["gitRef"])


def cache_root() -> Path:
    return Path(os.environ.get("QA18_RELEASE_CACHE", Path.home() / ".cache" / "pxh-qa18" / "releases"))


@dataclass(frozen=True)
class ReleaseTree:
    ref: str
    root: Path

    @property
    def node_app(self) -> Path:
        return self.root / "backend" / "nodejs" / "apps"

    @property
    def python_root(self) -> Path:
        return self.root / "backend" / "python"

    def node_process(self, env: dict[str, str], log: Path) -> stack.Process:
        server = self.node_app / "tests" / "support" / "harness" / "fault-stack-server.ts"
        ts_node = self.node_app / "node_modules" / ".bin" / "ts-node"
        release_env = {**env, "PORT": str(stack.NODE_PORT), "TS_NODE_PROJECT": str(self.node_app / "tsconfig.test.json")}
        return stack.Process("node-n", [str(ts_node), str(server)], self.node_app, release_env, log,
                             f"FAULT NODE READY {stack.NODE_PORT}")

    def worker_process(self, env: dict[str, str], run_dir: Path) -> stack.Process:
        worker = self.root / "integration-tests" / "harness" / "fault" / "worker.py"
        release_env = {**env, "PYTHONPATH": str(self.python_root)}
        python = release_python(self)
        return stack.Process("worker-n", [str(python), str(worker), str(run_dir)], self.python_root, release_env,
                             run_dir / "worker.log", "worker.started")


# What install.sh installs from: torch's CPU builds are served by the PyTorch index only.
INDEXES = ("--index-url", "https://pypi.org/simple", "--extra-index-url", "https://download.pytorch.org/whl/cpu")


def dropped_requirements(pyproject: str, base: Path) -> list[str]:
    """The requirements of a release's ``pyproject.toml`` whose package ``base`` (a site-packages) does not hold."""
    have = {canonicalize_name(d.metadata["Name"]) for d in metadata.distributions(path=[str(base)])}
    declared = [Requirement(r) for r in tomllib.loads(pyproject)["project"]["dependencies"]]
    return [str(r) for r in declared if r.url is None and canonicalize_name(r.name) not in have]


def lock_lines(report: dict[str, object]) -> list[str]:
    """A ``pip install --report``'s installs as hashed lock lines, sorted; refuses one with no sha256."""
    lines: list[str] = []
    for item in report["install"]:  # type: ignore[union-attr]
        name, version = item["metadata"]["name"], item["metadata"]["version"]
        sha = (item.get("download_info", {}).get("archive_info", {}).get("hashes") or {}).get("sha256")
        if not sha:
            raise RuntimeError(f"{name} {version} came with no sha256: a release lock pins every file")
        lines.append(f"{canonicalize_name(name)}=={version} --hash=sha256:{sha}")
    return sorted(lines)


def release_python(tree: ReleaseTree) -> Path:
    """The interpreter of the release's own virtualenv, made once per (release, this tree's virtualenv)."""
    base = Path(sysconfig.get_paths()["purelib"])
    pyproject = (tree.python_root / "pyproject.toml").read_text()
    installed = sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions(path=[str(base)]))
    key = hashlib.sha256("\n".join([str(base), pyproject, *installed]).encode()).hexdigest()[:12]
    venv = tree.root / f"venv-{key}"
    python = venv / "bin" / "python"
    if (venv / ".complete").exists():
        return python
    subprocess.run([getattr(sys, "_base_executable", sys.executable), "-m", "venv", "--clear", "--without-pip",
                    str(venv)], check=True)
    own = Path(subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                              capture_output=True, text=True, check=True).stdout.strip())
    # After the release's own packages: a .pth line is appended to sys.path behind the directory that holds it.
    (own / "zz-this-tree.pth").write_text(f"{base}\n")
    pip = [sys.executable, "-m", "pip", "--python", str(python), "--disable-pip-version-check", "--no-input"]
    lock = venv / "release.lock"
    wanted = dropped_requirements(pyproject, base)
    if wanted:
        report = venv / "resolve.json"
        subprocess.run([*pip, "install", "--dry-run", "--quiet", "--report", str(report), *INDEXES, *wanted],
                       check=True)
        lock.write_text("\n".join(lock_lines(json.loads(report.read_text()))) + "\n")
        # --ignore-installed: never uninstall this tree's copy of a package the release pins differently.
        subprocess.run([*pip, "install", "--quiet", "--no-deps", "--require-hashes", "--ignore-installed",
                        *INDEXES, "-r", str(lock)], check=True)
    else:
        lock.write_text("")
    (venv / ".complete").write_text(tree.ref)
    return python


def extract(ref: str) -> ReleaseTree:
    root = cache_root() / ref
    tree = ReleaseTree(ref, root)
    if not (root / ".complete").exists():
        root.mkdir(parents=True, exist_ok=True)
        archive = subprocess.Popen(["git", "-C", str(stack.REPO), "archive", ref, *PATHS], stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", str(root)], stdin=archive.stdout, check=True)
        if archive.wait() != 0:
            raise RuntimeError(f"git archive {ref} failed")
        (root / ".complete").write_text(ref)
    modules = tree.node_app / "node_modules"
    if not modules.exists():
        # A link left by a worktree that is gone dangles: it does not exist, yet a new link would collide with it.
        modules.unlink(missing_ok=True)
        modules.symlink_to(stack.NODE_APP / "node_modules")
    return tree
