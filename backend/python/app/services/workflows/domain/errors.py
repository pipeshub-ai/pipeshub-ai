"""Workflow-layer error hierarchy. Pure Python, zero infra imports."""
from __future__ import annotations


class WorkflowError(Exception):
    """Base for all workflow-service errors."""


class WorkflowNotFoundError(WorkflowError):
    def __init__(self, workflow_id: str) -> None:
        super().__init__(f"Workflow not found: {workflow_id}")
        self.workflow_id = workflow_id


class WorkflowVersionNotFoundError(WorkflowError):
    def __init__(self, version_id: str) -> None:
        super().__init__(f"WorkflowVersion not found: {version_id}")
        self.version_id = version_id


class WorkflowVersionConflictError(WorkflowError):
    """Raised when writing a version would overwrite an existing one, or when
    a pin lost a race to a concurrently saved newer version."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class WorkflowCodegenError(WorkflowError):
    """Raised when generation or verification of workflow code fails.
    Distinct from `WorkflowNotFoundError` so the HTTP layer can answer 422
    instead of 404."""

    def __init__(self, message: str, errors: list | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class FilterValidationError(WorkflowError):
    def __init__(self, field: str, code: str, fix_hint: str) -> None:
        super().__init__(f"Filter validation failed on '{field}': {fix_hint}")
        self.field = field
        self.code = code
        self.fix_hint = fix_hint


class VersionStoreUnavailableError(WorkflowError):
    """Raised when the version store cannot be reached or queried.
    Maps to 503 at the HTTP layer so the frontend can distinguish 'no versions
    exist' (200 with empty list) from 'the store is down' (503)."""

    def __init__(self, workflow_id: str, reason: str | None = None) -> None:
        detail = f"Version store unavailable for workflow {workflow_id}"
        if reason:
            detail = f"{detail}: {reason}"
        super().__init__(detail)
        self.workflow_id = workflow_id


class PinFailedError(WorkflowError):
    """Raised when a version was saved successfully but pinning it on the task
    failed. The version is retained (not discarded) so it can be listed and
    activated later."""

    def __init__(self, version: "WorkflowVersion", cause: Exception) -> None:  # noqa: F821
        super().__init__(
            f"Version {version.version_id} saved but could not be pinned: {cause}"
        )
        self.version = version
        self.__cause__ = cause


class ConversationWriteError(WorkflowError):
    """Raised when write-back to the originating conversation fails.
    Must NOT propagate to the caller's run state transition."""
