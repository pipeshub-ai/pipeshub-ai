"""Typed refusals of artifact publication. Each carries a stable ``reason_code`` for logs."""

from __future__ import annotations


class ArtifactPublishRefused(Exception):
    reason_code: str = "artifact_publish_refused"


class ArtifactVersionConflict(ArtifactPublishRefused):
    """The artifact moved past the version the writer started from; nothing was overwritten."""

    reason_code = "artifact_version_conflict"

    def __init__(self, artifact_id: str, *, expected: int | None, current: int | None) -> None:
        """``None``: the registry raced past its own bounded retry, versions not known."""
        super().__init__(f"artifact {artifact_id} is at version {current}, expected {expected}")
        self.artifact_id = artifact_id
        self.expected = expected
        self.current = current


class ArtifactTooLarge(ArtifactPublishRefused):
    reason_code = "artifact_too_large"


class ArtifactNotWritable(ArtifactPublishRefused):
    """Missing, of another tenant, or not writable by the run's actor (indistinguishable on purpose)."""

    reason_code = "artifact_not_writable"


__all__ = [
    "ArtifactNotWritable",
    "ArtifactPublishRefused",
    "ArtifactTooLarge",
    "ArtifactVersionConflict",
]
