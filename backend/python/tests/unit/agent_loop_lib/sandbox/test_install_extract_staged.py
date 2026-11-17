"""A package install is extracted aside and merged in only when complete, so
one refused at the size limit leaves the installed packages as they were."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox, _RunHandle

from .test_docker_coding_sandbox import _fake_container, _make_tar

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _install(tmp_path: Path, archive: bytes) -> tuple[int, str, str]:
    container = _fake_container()
    container.get_archive.side_effect = lambda path: (iter([archive]), {})
    client = MagicMock()
    client.containers.create.return_value = container
    sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
    return sandbox._install_in_container(
        client, "egress", "/deps", str(tmp_path / "deps"), ["pkg"], {"command": ["true"]}, _RunHandle(),
    )


def test_a_refused_install_leaves_the_installed_packages_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ef, "_oom_score_adj_usable", True)
    monkeypatch.setenv("SANDBOX_DISK_QUOTA", "off")
    deps = tmp_path / "deps" / "pkg"
    deps.mkdir(parents=True)
    (deps / "__init__.py").write_text("from .impl import f\n")
    (deps / "impl.py").write_text("def f(): return 1\n")
    monkeypatch.setenv("SANDBOX_DEPS_MAX_TOTAL_BYTES", "100")
    archive = _make_tar({"deps/pkg/__init__.py": b"from .core import g\n", "deps/pkg/core.py": b"x" * 500})

    code, _out, err = _install(tmp_path, archive)

    assert code != 0
    assert "Not all output was collected" in err
    assert (deps / "__init__.py").read_text() == "from .impl import f\n"
    assert not (deps / "core.py").exists()
    assert not [p for p in (tmp_path / "deps").parent.iterdir() if p.name.startswith(".install-")]


def test_a_complete_install_is_merged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ef, "_oom_score_adj_usable", True)
    monkeypatch.setenv("SANDBOX_DISK_QUOTA", "off")
    old = tmp_path / "deps" / "old"
    old.mkdir(parents=True)
    (old / "a.py").write_text("old")
    (tmp_path / "deps" / "pkg").mkdir()
    (tmp_path / "deps" / "pkg" / "__init__.py").write_text("v1")
    archive = _make_tar({"deps/pkg/__init__.py": b"v2", "deps/new/b.py": b"new"})

    code, _out, _err = _install(tmp_path, archive)

    assert code == 0
    assert (old / "a.py").read_text() == "old"
    assert (tmp_path / "deps" / "pkg" / "__init__.py").read_text() == "v2"
    assert (tmp_path / "deps" / "new" / "b.py").read_text() == "new"
