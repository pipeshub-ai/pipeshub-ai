"""Exactly one installed distribution may own files under litellm/.

crawl4ai pins `unclecode-litellm`, a fork that installs into the same top-level litellm/ directory as
the real litellm. Installed side by side, the files of whichever lands last win and fork-only files stay
behind, so the tree is a mix of two releases (the symptom was `NameError: name 'List' is not defined`
in litellm/fine_tuning/main.py). pyproject.toml excludes the fork; these tests keep it excluded and
catch any other distribution that starts writing into litellm/.
"""

import importlib.metadata
import re
import shutil
import subprocess
import tomllib
from collections import defaultdict
from pathlib import Path

import pytest

BACKEND_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"
REPO_ROOT = Path(__file__).resolve().parents[4]
FORK = "unclecode-litellm"


def litellm_owners(distributions=None) -> dict[str, list[str]]:
    """Map each distribution that records a file under litellm/ to those files."""
    owners: dict[str, list[str]] = defaultdict(list)
    for dist in distributions if distributions is not None else importlib.metadata.distributions():
        name = (dist.metadata["Name"] or "").lower().replace("_", "-")
        for file in dist.files or []:
            if file.parts and file.parts[0] == "litellm":
                owners[name].append(str(file))
    return dict(owners)


class _FakeFile:
    def __init__(self, path: str) -> None:
        self.parts = tuple(path.split("/"))

    def __str__(self) -> str:
        return "/".join(self.parts)


class _FakeDist:
    def __init__(self, name: str, files: list[str]) -> None:
        self.metadata = {"Name": name}
        self.files = [_FakeFile(f) for f in files]


def test_one_distribution_owns_litellm() -> None:
    owners = litellm_owners()
    assert len(owners) <= 1, (
        f"{sorted(owners)} all install files into litellm/; keep one: uninstall them and litellm, then install "
        f"the lock again (requirements/install.sh; CONTRIBUTING.md has the command)"
    )
    if owners:
        assert next(iter(owners)) == "litellm"


def test_guard_detects_a_second_owner() -> None:
    fork = _FakeDist(FORK, ["litellm/__init__.py", "litellm/only_in_fork.py", "unclecode_litellm-1.dist-info/RECORD"])
    real = _FakeDist("litellm", ["litellm/__init__.py", "litellm_proxy_extras/x.py"])
    assert sorted(litellm_owners([fork, real])) == ["litellm", FORK]
    assert list(litellm_owners([real])) == ["litellm"]


def test_manifest_excludes_the_fork_and_keeps_the_real_package() -> None:
    data = tomllib.loads(BACKEND_MANIFEST.read_text(encoding="utf-8"))
    assert FORK in data["tool"]["uv"]["exclude-dependencies"]
    assert any(d.replace(" ", "").lower().startswith("litellm") for d in data["project"]["dependencies"])


# The images and a contributor's venv install from the hashed locks (requirements/install.sh: pip --require-hashes
# --no-deps), not with uv, so the repair is the lock's own: the fork and litellm leave together (removing the fork
# deletes files litellm owns too), then the lock installs the locked litellm whole. The locks never hold the fork
# (PY-LOCK-AGREE refuses an excluded package), so the reinstall cannot bring it back.
_INSTALL = re.compile(r"requirements/install\.sh (runtime|dev)\b")


@pytest.mark.parametrize("path", ["Dockerfile", "deployment/docker-compose/hot-reload/Dockerfile.dev"])
def test_images_built_over_an_older_base_drop_the_fork(path) -> None:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    uninstall = text.index(f"pip uninstall -y --disable-pip-version-check {FORK} litellm")
    install = _INSTALL.search(text)
    assert install is not None and uninstall < install.start()


def test_the_locks_hold_the_real_litellm_and_never_the_fork() -> None:
    for lock in ("runtime", "dev"):
        text = (BACKEND_MANIFEST.parent / "requirements" / f"{lock}.lock").read_text(encoding="utf-8")
        assert re.search(r"^litellm==", text, re.M), lock
        assert not re.search(rf"^{FORK}==", text, re.M), lock


def _bash_repair() -> str:
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.S)
    (block,) = [b for b in blocks if f"uninstall -y {FORK}" in b]
    return block


def _run_repair(tmp_path: Path, uninstall_exit: int) -> list[str]:
    """Run the documented bash repair in a stand-in checkout (a stub venv python and install.sh); return their calls."""
    calls = tmp_path / "calls"
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "requirements").mkdir()
    stubs = {
        tmp_path / "venv" / "bin" / "python": f'#!/bin/sh\necho "python $*" >> "{calls}"\nexit {uninstall_exit}\n',
        tmp_path / "requirements" / "install.sh": f'#!/bin/sh\necho "install.sh $* PYTHON=$PYTHON" >> "{calls}"\n',
    }
    for stub, body in stubs.items():
        stub.write_text(body)
        stub.chmod(0o755)
    subprocess.run(["bash", "-c", _bash_repair()], cwd=tmp_path, timeout=30, check=False)
    return calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_repair_removes_both_then_installs_the_lock(tmp_path) -> None:
    assert _run_repair(tmp_path, 0) == [
        f"python -m pip uninstall -y {FORK} litellm",
        "install.sh dev --project . --editable PYTHON=venv/bin/python",
    ]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_repair_installs_nothing_when_the_uninstall_fails(tmp_path) -> None:
    assert _run_repair(tmp_path, 1) == [f"python -m pip uninstall -y {FORK} litellm"]
