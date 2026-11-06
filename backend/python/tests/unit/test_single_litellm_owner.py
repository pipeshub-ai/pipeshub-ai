"""Exactly one installed distribution may own files under litellm/.

crawl4ai pins `unclecode-litellm`, a fork that installs into the same top-level litellm/ directory as
the real litellm. Installed side by side, the files of whichever lands last win and fork-only files stay
behind, so the tree is a mix of two releases (the symptom was `NameError: name 'List' is not defined`
in litellm/fine_tuning/main.py). pyproject.toml excludes the fork; these tests keep it excluded and
catch any other distribution that starts writing into litellm/.
"""

import importlib.metadata
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
        f"{sorted(owners)} all install files into litellm/; keep one (uninstall the others, "
        f"then reinstall the same litellm: uv pip install --reinstall-package litellm "
        f"litellm=={importlib.metadata.version('litellm')})"
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


@pytest.mark.parametrize("path", ["Dockerfile", "deployment/docker-compose/hot-reload/Dockerfile.dev"])
def test_images_built_over_an_older_base_drop_the_fork(path) -> None:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    uninstall = text.index(f"uv pip uninstall --system {FORK}")
    assert uninstall < text.index("--reinstall-package litellm -e .")
