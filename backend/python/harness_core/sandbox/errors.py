"""Typed sandbox errors. Each has a stable wire ``code`` so a remote client raises the same type."""

from __future__ import annotations

from typing import ClassVar

from harness_core.refusals import HarnessRefusal


class SandboxError(Exception):
    code: ClassVar[str] = "sandbox_error"


class SandboxRefusal(SandboxError, HarnessRefusal):
    """The broker declined the request; ``reason`` is a machine-readable reason code."""

    code: ClassVar[str] = "refused"

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.reason_code = reason


class IsolationUnavailable(SandboxRefusal):
    code: ClassVar[str] = "isolation_unavailable"


class SandboxProviderUnavailable(SandboxRefusal):
    """The deployment names a sandbox provider this install cannot run (a configuration error, never retried)."""

    code: ClassVar[str] = "provider_unavailable"


class PolicyUnenforceable(SandboxRefusal):
    code: ClassVar[str] = "policy_unenforceable"


class ImageNotAllowed(SandboxRefusal):
    code: ClassVar[str] = "image_not_allowed"


class InvalidSandboxSpec(SandboxRefusal):
    code: ClassVar[str] = "invalid_spec"


class SandboxPolicyViolation(SandboxRefusal):
    """An adapter's own create request broke its hardening policy (a bug, never user input)."""

    code: ClassVar[str] = "policy_violation"


class LeaseFenced(SandboxRefusal):
    code: ClassVar[str] = "lease_fenced"


class SandboxNotFound(SandboxError):
    code: ClassVar[str] = "not_found"


class SandboxPathNotFound(SandboxError):
    code: ClassVar[str] = "path_not_found"


class SandboxConnectError(SandboxError):
    code: ClassVar[str] = "connect_failed"


class InvalidSandboxRequest(SandboxError):
    code: ClassVar[str] = "invalid_request"


class SandboxReleaseIncomplete(SandboxError):
    """A release could not save the sandbox's persistent changes; the broker keeps it and tries again."""

    code: ClassVar[str] = "release_incomplete"


def memory_limit_text(mb: int) -> str:
    """A memory limit as a person reads it, as the chat words it: whole gigabytes as GB, anything else in MB."""
    return f"{mb // 1024} GB" if mb % 1024 == 0 else f"{mb} MB"


class SandboxOutOfMemory(SandboxError):
    """A sandbox's memory limit ended the work in it: under runc the kernel killed a process (the engine's own, for a
    run), under gVisor the whole sandbox, which the host sees as one process. Never retried: the same work in a new
    sandbox of the same size meets the same limit (R32-SECCOMP, R31GVISO-FU-3)."""

    code: ClassVar[str] = "out_of_memory"
    reason_code: ClassVar[str] = "out_of_memory"

    def __init__(self, message: str, *, memory_mb: int) -> None:
        super().__init__(message)
        self.memory_mb = memory_mb


class BrokerUnavailable(SandboxError):
    code: ClassVar[str] = "unavailable"


class BrokerUnauthorized(SandboxError):
    code: ClassVar[str] = "unauthorized"


ERROR_TYPES: tuple[type[SandboxError], ...] = (
    SandboxError,
    SandboxRefusal,
    IsolationUnavailable,
    SandboxProviderUnavailable,
    PolicyUnenforceable,
    ImageNotAllowed,
    InvalidSandboxSpec,
    SandboxPolicyViolation,
    LeaseFenced,
    SandboxNotFound,
    SandboxPathNotFound,
    SandboxConnectError,
    InvalidSandboxRequest,
    SandboxReleaseIncomplete,
    BrokerUnavailable,
    BrokerUnauthorized,
)

__all__ = [
    "ERROR_TYPES",
    "BrokerUnauthorized",
    "BrokerUnavailable",
    "ImageNotAllowed",
    "InvalidSandboxRequest",
    "InvalidSandboxSpec",
    "IsolationUnavailable",
    "LeaseFenced",
    "PolicyUnenforceable",
    "SandboxConnectError",
    "SandboxError",
    "SandboxNotFound",
    "SandboxOutOfMemory",
    "memory_limit_text",
    "SandboxPathNotFound",
    "SandboxPolicyViolation",
    "SandboxProviderUnavailable",
    "SandboxRefusal",
    "SandboxReleaseIncomplete",
]
