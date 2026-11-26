"""``EngineCheckpointStore`` over a ``BinaryObjectStore``.

The controller passes the session workspace's object scope; with a
``SealedObjectStore`` (PipesHub) the state is sealed with the session's data key,
so erasing the session (crypto-shred, E7) makes it unreadable at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineStateUnavailable
from harness_core.engines.events import EngineState, EngineStateBlob
from harness_core.workspace.types import ObjectUnreadable, WorkspaceKeyDestroyed

if TYPE_CHECKING:
    from harness_core.workspace.ports import BinaryObjectStore
    from harness_core.workspace.types import ObjectScope

STATE_MISSING = "state_missing"
STATE_CORRUPT = "state_corrupt"


class ObjectStoreEngineCheckpoints:
    def __init__(self, objects: BinaryObjectStore) -> None:
        self._objects = objects

    async def save(self, scope: ObjectScope, state: EngineState) -> EngineStateBlob:
        ref = await self._objects.put(scope, state.payload)
        return EngineStateBlob(engine=state.engine, schema_version=state.schema_version, content=ref)

    async def load(self, scope: ObjectScope, blob: EngineStateBlob) -> EngineState:
        try:
            payload = await self._objects.get(scope, blob.content)
        except (WorkspaceKeyDestroyed, FileNotFoundError, LookupError) as exc:
            raise EngineStateUnavailable("saved engine state is gone", reason=STATE_MISSING) from exc
        # Every object store verifies bytes against the ref (BinaryObjectStoreContract): a mismatch raises here.
        except (ObjectUnreadable, ValueError) as exc:
            raise EngineStateUnavailable("saved engine state does not verify", reason=STATE_CORRUPT) from exc
        return EngineState(engine=blob.engine, schema_version=blob.schema_version, payload=payload)


__all__ = ["STATE_CORRUPT", "STATE_MISSING", "ObjectStoreEngineCheckpoints"]
