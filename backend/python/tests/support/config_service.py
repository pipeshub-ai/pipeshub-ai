"""A configuration service fake for the eval and replay rigs (DUR2-C1..C4).

The rigs used a ``MagicMock`` (or an ``AsyncMock`` answering ``{}``): a read the
mock could not answer raised ``object MagicMock can't be used in 'await'
expression`` inside the product's guarded readers, which logged it and fell back
to the flag's default, so a rig run looked like a run on a deployment whose
settings are unreadable. This fake answers like ``ConfigurationService.get_config``
from declared nodes: the platform settings carry every platform flag the product
defines, set explicitly; a key nobody declared returns the caller's default and
is recorded in ``misses``, so a test can assert a run read nothing undeclared.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# Every platform flag the product defines, at the value a rig runs with. A flag added to
# ``CONFIG`` fails ``test_rig_config`` until it is declared here.
RIG_PLATFORM_FLAGS: dict[str, bool] = {
    CONFIG.ENABLE_WORKFLOW_BUILDER: False,
    CONFIG.ENABLE_BETA_CONNECTORS: False,
    CONFIG.ENABLE_MCP: False,
    CONFIG.ENABLE_ACTIONS: True,
    CONFIG.ENABLE_VECTOR_STORE_REBUILD: False,
    CONFIG.ENABLE_SKILLS: True,
    CONFIG.ENABLE_USER_CONTEXT: True,
    CONFIG.ENABLE_KNOWLEDGE_PUBLICATIONS: False,
    # The rigs run as an org with the agent harness on (ADR-OPS-08), as they run with its switch on.
    CONFIG.ENABLE_AGENT_HARNESS: True,
    CONFIG.ENABLE_SOFT_DELETE: False,
    CONFIG.ENABLE_NAMED_ENTITY_EXTRACTION: False,
    CONFIG.ENABLE_KH_SCOPE_LISTING: False,
    # Main's flags, at their release defaults (`PLATFORM_FLAG_DEFAULTS`, `UNLISTED_FLAG_DEFAULTS`; the artifact
    # read modes are off unless set).
    CONFIG.ENABLE_API_TOOLSETS: False,
    CONFIG.ENABLE_CLASSIFY_ON_FIRST_USE: False,
    CONFIG.ENABLE_ARTIFACT_WRITE: False,
    CONFIG.ENABLE_ARTIFACT_READ: False,
    CONFIG.ENABLE_ARTIFACT_READ_SHADOW: False,
    CONFIG.ENABLE_KB_PUSH: False,
}


def platform_flag_names() -> set[str]:
    return {value for name, value in vars(CONFIG).items() if name.startswith("ENABLE_") and isinstance(value, str)}


class FakeConfigurationService:
    """``get_config``/``set_config`` over declared nodes; ``prefixes`` answer every key under them."""

    def __init__(
        self,
        nodes: Mapping[str, Any] | None = None,
        *,
        flags: Mapping[str, bool] | None = None,
        prefixes: Mapping[str, Callable[[str], Any]] | None = None,
    ) -> None:
        self._nodes: dict[str, Any] = {
            PLATFORM_SETTINGS_KEY: {"featureFlags": {**RIG_PLATFORM_FLAGS, **(flags or {})}},
            # The rigs keep blobs in fakes, never on local disk. Unset reads as local, and main's knowledge
            # search (#3568 pattern match) would then grep this host's storage directory.
            config_node_constants.STORAGE.value: {"storageType": "s3"},
            # The fixture collection the rigs' agents search (`harness_replay.knowledge_base`): a collection has no
            # connector config, which is what the store holds for it.
            "/services/connectors/kb-eval/config": None,
            # No gateway identity policy saved (main #4428): a durable run binds its actor and reads it.
            config_node_constants.AI_GATEWAY_IDENTITY_POLICY.value: None,
            # No service endpoints saved: a durable run's write disclosure (OWD-18) names its session without a link.
            config_node_constants.ENDPOINTS.value: {},
            **(nodes or {}),
        }
        self._prefixes = dict(prefixes or {})
        self.misses: list[str] = []
        self.reads: list[str] = []
        self._listeners: list[Callable[[str], None]] = []

    def add_change_listener(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)

    def notify(self, key: str) -> None:
        """The store's change notification for ``key``, as a write or delete by another process publishes it."""
        for listener in self._listeners:
            listener(key)

    def put(self, key: str, value: Any) -> None:  # noqa: ANN401
        self._nodes[key] = copy.deepcopy(value)

    def answer(self, prefix: str, answer: Callable[[str], Any]) -> None:
        """Every key under ``prefix`` is answered by ``answer(key)``; the latest declaration wins."""
        others = {key: value for key, value in self._prefixes.items() if key != prefix}
        self._prefixes = {prefix: answer, **others}

    async def get_config(
        self,
        key: str,
        default: Any = None,  # noqa: ANN401
        use_cache: bool = False,
        *,
        raise_on_error: bool = False,
        keep_in_cache: bool = True,
    ) -> Any:  # noqa: ANN401
        self.reads.append(key)
        if key in self._nodes:
            return copy.deepcopy(self._nodes[key])
        for prefix, answer in self._prefixes.items():
            if key.startswith(prefix):
                return answer(key)
        self.misses.append(key)
        return default

    async def set_config(
        self, key: str, value: Any, *, ttl_seconds: int | None = None, keep_in_cache: bool = True,  # noqa: ANN401
    ) -> bool:
        self._nodes[key] = copy.deepcopy(value)
        return True

    async def update_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        return await self.set_config(key, value)

    async def create_config_if_absent(self, key: str, value: Any) -> bool:  # noqa: ANN401
        if key in self._nodes:
            return False
        return await self.set_config(key, value)

    async def delete_config(self, key: str) -> bool:
        return self._nodes.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [key for key in self._nodes if key.startswith(directory)]


__all__ = ["RIG_PLATFORM_FLAGS", "FakeConfigurationService", "platform_flag_names"]
