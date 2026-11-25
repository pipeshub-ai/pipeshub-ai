"""``ImageBinaryReader`` on a Docker daemon (EH2): a throwaway container of the image id reads the file.

The container is as hardened as a sandbox, has no network, and gets the sandbox's ``/tmp`` tmpfs: a link into
``/tmp`` then resolves to a file a sandbox could write, and the broker never vouches for one (``vouched_binaries``).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

from harness_core.engines.binary import BinaryObservation, parse_sha256sum
from harness_core.sandbox.errors import BrokerUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.harness.sandbox.docker_client import DockerClientProvider

# $1 is the path. Line 1: where it resolves; line 2: the hash. Exit 3: no regular file the user can read.
_OBSERVE = 'r=$(readlink -f "$1") || r=$1; printf "%s\\n" "$r"; [ -f "$r" ] && [ -r "$r" ] || exit 3; exec sha256sum "$r"'
_MEMORY = 256 * 1024 * 1024
TMPFS = "/tmp"


class DockerImageBinaryReader:
    def __init__(
        self,
        provider: DockerClientProvider,
        *,
        user: str,
        labels: Callable[[], Mapping[str, str]],
        timeout_s: int = 120,
    ) -> None:
        self._provider = provider
        self._user = user
        self._labels = labels
        self._timeout_s = timeout_s

    async def observe(self, image_id: str, path: str) -> BinaryObservation:
        api: Any = self._provider.client.api
        container: str | None = None
        try:
            created = await self._provider.run_blocking(
                api.create_container,
                image_id,
                command=["/bin/sh", "-c", _OBSERVE, "observe", path],
                entrypoint=[],
                user=self._user,
                labels=dict(self._labels()),
                host_config=api.create_host_config(
                    read_only=True,
                    tmpfs={TMPFS: "size=1m,mode=1777,noexec,nosuid,nodev"},
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    pids_limit=16,
                    mem_limit=_MEMORY,
                    memswap_limit=_MEMORY,
                    nano_cpus=int(1e9),
                    network_mode="none",
                ),
                network_disabled=True,
            )
            container = cast("str", created["Id"])
            await self._provider.run_blocking(api.start, container)
            waited = await self._provider.run_blocking(api.wait, container, timeout=self._timeout_s)
            code = int(cast("dict[str, Any]", waited).get("StatusCode", -1))
            printed = cast("bytes", await self._provider.run_blocking(api.logs, container, stdout=True, stderr=False))
        except Exception as exc:
            raise BrokerUnavailable(f"could not read {path} from image {image_id}: {type(exc).__name__}") from exc
        finally:
            if container is not None:
                with contextlib.suppress(Exception):
                    await self._provider.run_blocking(api.remove_container, container, force=True)
        return _observation(path, code, printed.decode("utf-8", "replace"))


def _observation(path: str, code: int, printed: str) -> BinaryObservation:
    # Killed (OOM, timeout) or never ran: no verdict on the image.
    if code < 0 or code >= 128:
        raise BrokerUnavailable(f"reading {path} exited with {code}")
    resolved, _, rest = printed.partition("\n")
    digest = parse_sha256sum(rest) if code == 0 else None
    return BinaryObservation(path, resolved.strip() or path, digest)


__all__ = ["TMPFS", "DockerImageBinaryReader"]
