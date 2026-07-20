"""Versioned artifact management — see the plan
(`.cursor/plans/artifact_versioning_system_05a2bca3.plan.md`) for the full
design. Public surface is `ArtifactRegistryService`; everything else in
this package is an internal collaborator it composes."""

from __future__ import annotations

from .access import AccessDeniedError, AccessPolicy, ArtifactNotFoundError
from .lineage import LineageTracker
from .models import Actor, ArtifactLineage, ArtifactMetadata, ArtifactVersion, UploadGrant
from .registry import MAX_ARTIFACT_BYTES, ArtifactRegistryService
from .signed_urls import GrantExpiredError, GrantVerificationError, SignedUrlBroker
from .versioning import VersionConflictError, VersionManager, VersionSyncError

__all__ = [
    "ArtifactRegistryService",
    "MAX_ARTIFACT_BYTES",
    "Actor",
    "ArtifactMetadata",
    "ArtifactVersion",
    "ArtifactLineage",
    "UploadGrant",
    "AccessPolicy",
    "AccessDeniedError",
    "ArtifactNotFoundError",
    "VersionManager",
    "VersionConflictError",
    "VersionSyncError",
    "LineageTracker",
    "SignedUrlBroker",
    "GrantExpiredError",
    "GrantVerificationError",
]
