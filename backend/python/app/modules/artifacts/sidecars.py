"""Enrichment sidecars: one stage's output for one content revision, beside its artifact set.

A sidecar is ``enrich/{name}@{version}.json`` under the revision's root, written by the
stage that produced it, after the revision's own files and not listed in its manifest.
The version is the stage's, so a reader can tell an output of an older stage from one it
understands, and two versions of a stage never overwrite each other's file.
"""

import logging
import re
from typing import TYPE_CHECKING, Any

from app.models.blocks import SemanticMetadata
from app.modules.artifacts.store import (
    ArtifactRoutesUnavailable,
    ArtifactStoreError,
    NodeArtifactStore,
    artifact_writes_available,
)
from app.modules.artifacts.writer import REV_PATTERN, canonical_json
from app.services.featureflag.platform_settings import is_artifact_write_enabled
from app.telemetry.modules.record_storage_metrics import record_artifact_write

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

CLASSIFICATION = "classification"
ENTITIES = "entities"

_JSON = "application/json"
# The storage service's sidecar path pattern.
_NAME = re.compile(r"^[a-z0-9_.-]+$")


def sidecar_path(name: str, version: int) -> str:
    return f"enrich/{name}@{version}.json"


def classification_payload(metadata: SemanticMetadata) -> dict[str, Any]:
    # The named-entity section is the entities stage's, in its own sidecar.
    return metadata.model_dump(mode="json", exclude={"named_entities"})


class ArtifactSidecars:
    def __init__(
        self,
        store: NodeArtifactStore,
        config_service: "ConfigurationService | None",
        logger: logging.Logger | None = None,
    ) -> None:
        self.store = store
        self.config_service = config_service
        self.logger = logger or logging.getLogger(__name__)

    async def write(
        self, org_id: str, virtual_record_id: str, rev: str | None, name: str, version: int, payload: dict[str, Any],
    ) -> bool:
        """Store ``payload`` as revision ``rev``'s ``name`` sidecar. True when it was stored.

        Never raises: the stage's own outputs are written already, and a missing sidecar
        only means a reader of the revision falls back to them.
        """
        if not virtual_record_id or not rev or not await is_artifact_write_enabled(self.config_service):
            return False
        if not artifact_writes_available():
            record_artifact_write("sidecar_unavailable")
            return False
        path = sidecar_path(name, version)
        try:
            if not REV_PATTERN.match(rev) or not _NAME.match(name):
                raise ValueError("revision or sidecar name the storage service would refuse")
            plan = await self.store.presign_revision(org_id, virtual_record_id, rev, [(path, _JSON)])
            await self.store.put(plan.files[path], canonical_json(payload), _JSON)
        except ArtifactRoutesUnavailable:
            record_artifact_write("sidecar_unavailable")
            return False
        except Exception as exc:
            # Only the type and our own messages: an aiohttp error can carry a signed URL.
            reason = str(exc) if isinstance(exc, (ArtifactStoreError, ValueError)) else ""
            self.logger.warning(
                "Sidecar %s of virtual record %s (rev %s) not written: %s %s",
                path, virtual_record_id, rev, type(exc).__name__, reason,
            )
            record_artifact_write("sidecar_failed")
            return False
        record_artifact_write("sidecar_written")
        return True
