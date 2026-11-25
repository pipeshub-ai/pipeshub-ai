"""IBR-1..IBR-3 (EH2) against ``DockerImageBinaryReader``: a throwaway container of a real image reads the file."""

from __future__ import annotations

import base64
import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import SANDBOX_USER
from app.harness.sandbox.docker_client import DockerClientProvider
from app.harness.sandbox.image_binaries import DockerImageBinaryReader
from harness_core.testing.contracts.image_binaries import (
    CONTRACT_FILES,
    ImageBinaryReaderContract,
)
from harness_core.testing.engines.binary import Directory, Link
from tests.support.docker_images import build_test_image

from .conftest import DOCKER_TEST_IMAGE, skip_without_docker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.testing.contracts.image_binaries import Subject

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

NAMESPACE = f"io.pipeshub.eh2ibr.{uuid.uuid4().hex[:8]}"


def _dockerfile(base: str) -> str:
    steps: list[str] = []
    for path, entry in CONTRACT_FILES.items():
        parent = path.rsplit("/", 1)[0]
        if isinstance(entry, Directory):
            steps.append(f"mkdir -p {path}")
        elif isinstance(entry, Link):
            steps.append(f"mkdir -p {parent} && ln -s {entry.target} {path}")
        else:
            encoded = base64.b64encode(entry).decode()
            steps.append(f"mkdir -p {parent} && echo {encoded} | base64 -d > {path} && chmod 0444 {path}")
    return f"FROM {base}\nUSER 0:0\nRUN {' && '.join(steps)}\nUSER 1000:1000\n"


@pytest.fixture(scope="module")
def provider() -> Iterator[DockerClientProvider]:
    # The image is built FROM the sandbox test image: a daemon without it (the unit job's runner) skips here, as the
    # rest of the Docker lane does, instead of erroring in the build.
    skip_without_docker()
    docker_provider = DockerClientProvider(max_workers=4)
    yield docker_provider
    api = docker_provider.client.api
    for info in api.containers(all=True, filters={"label": [f"{NAMESPACE}.managed=1"]}):
        api.remove_container(info["Id"], force=True)
    docker_provider.close()


@pytest.fixture(scope="module")
def image_id(provider: DockerClientProvider) -> Iterator[str]:
    tag = f"pxh-eh2-ibr:{uuid.uuid4().hex[:8]}"
    image = provider.client.images.get(build_test_image(provider.client, tag, _dockerfile(DOCKER_TEST_IMAGE)))
    try:
        yield image.id
    finally:
        provider.client.images.remove(tag, force=True)


class TestDockerImageBinaryReader(ImageBinaryReaderContract):
    @pytest.fixture
    def subject(self, provider: DockerClientProvider, image_id: str) -> Subject:
        reader = DockerImageBinaryReader(
            provider, user=SANDBOX_USER, labels=lambda: {f"{NAMESPACE}.managed": "1", "pipeshub.harness.owner": "x-lead"}
        )
        return reader, image_id


async def test_ibr_the_throwaway_containers_are_removed(provider: DockerClientProvider, image_id: str) -> None:
    reader = DockerImageBinaryReader(provider, user=SANDBOX_USER, labels=lambda: {f"{NAMESPACE}.managed": "1"})
    _ = await reader.observe(image_id, next(iter(CONTRACT_FILES)))
    assert provider.client.api.containers(all=True, filters={"label": [f"{NAMESPACE}.managed=1"]}) == []
