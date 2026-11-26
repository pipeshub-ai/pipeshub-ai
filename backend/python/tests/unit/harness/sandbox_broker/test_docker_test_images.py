"""Test images built on a shared base skip the build cache (``tests/support/docker_images.py``).

Two xdist workers building the same Dockerfile shared one cached image; the first to finish force-removed it, and
the other's MCP runner build failed with ``failed to read config content: NotFound``. No Docker here: the build
call is recorded, and the test tree is scanned so no Docker test builds around the helper.
"""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path
from typing import Any

from tests.support.docker_images import build_test_image

TESTS = Path(__file__).resolve().parents[3]
HELPER = TESTS / "support" / "docker_images.py"


class _Images:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.calls.append(kwargs)


class _Client:
    def __init__(self) -> None:
        self.images = _Images()


def test_a_test_image_is_built_without_the_cache_from_local_images_only() -> None:
    client = _Client()
    tag = build_test_image(client, "pxh-t:1", "FROM base\nCOPY a.py /a.py\n", files={"a.py": b"x"}, labels={"k": "v"})
    assert tag == "pxh-t:1"
    (call,) = client.images.calls
    assert call["nocache"] is True
    assert call["pull"] is False
    assert call["labels"] == {"k": "v"}
    assert call["tag"] == "pxh-t:1"
    with tarfile.open(fileobj=io.BytesIO(call["fileobj"].getvalue())) as archive:
        assert {m.name for m in archive.getmembers()} == {"Dockerfile", "a.py"}
        dockerfile = archive.extractfile("Dockerfile")
        assert dockerfile is not None and dockerfile.read() == b"FROM base\nCOPY a.py /a.py\n"


def test_no_docker_test_builds_an_image_around_the_helper() -> None:
    builds = [
        str(path.relative_to(TESTS))
        for path in TESTS.rglob("*.py")
        if path != HELPER and re.search(r"\.images\.build\(", path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert builds == [], f"build test images with tests.support.docker_images.build_test_image: {builds}"
