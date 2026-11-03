"""Errors every vector provider raises when a write or scoped read does not name its organisation."""

from __future__ import annotations

from app.exceptions.non_retryable import NonRetryableError


class VectorOrgError(NonRetryableError):
    """A write or scoped read arrived without, or against the wrong, organisation."""


class OrgRequired(VectorOrgError):
    pass


class OrgMismatch(VectorOrgError):
    pass
