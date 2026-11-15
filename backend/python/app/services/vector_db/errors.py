"""Errors every vector provider raises when a write or scoped read does not name its organisation."""

from __future__ import annotations

from app.exceptions.non_retryable import NonRetryableError


class VectorOrgError(NonRetryableError):
    """A write or scoped read arrived without, or against the wrong, organisation."""


class OrgRequired(VectorOrgError):
    pass


class OrgMismatch(VectorOrgError):
    pass


class SpaceMismatch(VectorOrgError):
    pass


class PartialResult(RuntimeError):
    """A store answered a read with fewer results than it holds (a query that ran out of time, a shard that failed), or said so. A caller
    that acts on "every point of these records" must treat it as a failure: the missing points would keep whatever they carry."""
