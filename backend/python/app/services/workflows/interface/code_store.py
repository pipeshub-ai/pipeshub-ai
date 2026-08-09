"""ICodeStore port — stores workflow source bundles (over ArtifactRegistryService)."""
from __future__ import annotations

from typing import Protocol

from app.services.workflows.domain.models import ArtifactRef


class ICodeStore(Protocol):
    async def put(self, workflow_id: str, org_id: str, source: bytes, *, content_type: str = "text/x-python") -> ArtifactRef:
        """Upload source bundle, return artifact reference."""
        ...

    async def get(self, ref: ArtifactRef) -> bytes:
        """Download source bundle by reference. Raises `KeyError` when absent."""
        ...

    async def delete(self, ref: ArtifactRef) -> bool:
        """Remove a source bundle. Only for cleaning up a bundle whose version
        row was never created; never for source a stored version references."""
        ...
