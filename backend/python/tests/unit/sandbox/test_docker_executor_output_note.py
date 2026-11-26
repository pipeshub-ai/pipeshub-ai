"""The executor tells its caller which output files it left out."""

from __future__ import annotations

import io
import tarfile
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from app.sandbox import docker_executor
from app.sandbox.docker_executor import DockerExecutor

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def test_an_output_over_the_file_limit_is_reported_in_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_OUTPUT_MAX_FILE_BYTES", "10")
    container = MagicMock()
    container.id = "c" * 64
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = iter([b""])
    container.get_archive.return_value = (
        iter([_tar({"output/big.bin": b"x" * 100, "output/ok.txt": b"ok"})]), {},
    )
    client = MagicMock()
    monkeypatch.setattr(docker_executor, "create_bounded_container", lambda *a, **k: container)
    monkeypatch.setattr(docker_executor, "start_disk_watch", lambda *a, **k: None)
    import docker

    monkeypatch.setattr(docker, "from_env", lambda: client)
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    out.mkdir()

    result = await DockerExecutor()._run_container(
        image="img", command=["true"], work_dir=str(tmp_path), src_dir=str(src),
        output_dir=str(out), env={}, timeout=5,
    )

    assert result.success
    assert (out / "ok.txt").read_bytes() == b"ok"
    assert not (out / "big.bin").exists()
    assert "output/big.bin" in result.stderr
    assert "Not all output was collected" in result.stderr
