"""`DockerCodingSandbox._ensure_image` error attribution.

A pull failure gets blamed on the sandbox image, but `ensure_image`
returning False cannot tell an absent image from a `DOCKER_HOST` nothing
is listening on: a dead endpoint fails `images.get` exactly like a missing
image. Reporting the image in that case sent an operator looking for an
image that was present all along, while the real cause -- the socket proxy
not running -- sat further down the same sentence.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding.base import CodingSandboxError
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

_PULL_ERROR = RuntimeError(
    "Error while fetching server API version: "
    "HTTPConnectionPool(host='docker-socket-proxy', port=2375)",
)


def _sandbox(*, reachable: bool) -> DockerCodingSandbox:
    provider = MagicMock()
    provider.ensure_image = AsyncMock(return_value=False)
    provider.pull_image = AsyncMock(side_effect=_PULL_ERROR)
    provider.ping = AsyncMock(return_value=reachable)
    provider.docker_host = MagicMock(return_value="tcp://docker-socket-proxy:2375")
    return DockerCodingSandbox(working_dir="/tmp/sandbox-test", provider=provider)


class TestEnsureImageErrorAttribution:
    async def test_an_unreachable_daemon_is_reported_as_such(self) -> None:
        with pytest.raises(CodingSandboxError) as excinfo:
            await _sandbox(reachable=False)._ensure_image()

        message = str(excinfo.value)
        assert "Docker is unreachable" in message
        assert "tcp://docker-socket-proxy:2375" in message
        # The pull failure is the evidence for the diagnosis, so it is carried.
        assert str(_PULL_ERROR) in message
        # Compose and Helm start the proxy differently; naming only one sends
        # half the readers to a profile their deployment does not have.
        assert "compose" in message.lower() and "helm" in message.lower()
        # The image is present in exactly this case, so naming it misleads.
        assert "is not available locally" not in message

    async def test_a_reachable_daemon_still_blames_the_image(self) -> None:
        with pytest.raises(CodingSandboxError) as excinfo:
            await _sandbox(reachable=True)._ensure_image()

        assert "is not available locally" in str(excinfo.value)
