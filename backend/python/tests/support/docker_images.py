"""Throwaway images the Docker tests build on top of a base image, with no build cache.

Two xdist workers that build the same Dockerfile on the same base get one image from the classic builder's cache;
the first to finish removes its tag with ``force=True``, which deletes that shared image, and the other's build or
container start then fails with ``failed to read config content: NotFound`` (seen on the containerd image store).
Every build here skips the cache, so each worker owns the image it removes.
"""

from __future__ import annotations

import io
import tarfile
from typing import Any


def build_test_image(
    client: Any,  # noqa: ANN401 - docker.DockerClient
    tag: str,
    dockerfile: str,
    *,
    files: dict[str, bytes] | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    """Builds ``dockerfile`` (with ``files`` in its context) as ``tag`` from local images only; returns ``tag``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in {"Dockerfile": dockerfile.encode(), **(files or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    _ = client.images.build(
        fileobj=buffer,
        custom_context=True,
        tag=tag,
        labels=labels or {},
        nocache=True,
        pull=False,
        rm=True,
        forcerm=True,
    )
    return tag
