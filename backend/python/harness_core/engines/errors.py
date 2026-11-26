"""Typed refusals of the engine layer. Each carries a stable ``reason_code`` for logs."""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class EngineRefusal(HarnessRefusal):
    reason_code: str = "engine_refused"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason_code = reason


class EngineVersionNotAllowed(EngineRefusal):
    reason_code = "version_not_allowed"


class EngineStateIncompatible(EngineRefusal):
    reason_code = "state_incompatible"


class EngineBinaryMismatch(EngineRefusal):
    reason_code = "binary_mismatch"


class ProfileWidening(EngineRefusal):
    """A layer asked for more than the layer above left; ``field`` names the dimension (wire name)."""

    reason_code = "profile_widening"

    def __init__(self, field: str, message: str, *, layer: str | None = None) -> None:
        super().__init__(f"{field}: {message}" if layer is None else f"{layer} {field}: {message}")
        self.field = field
        self.layer = layer
        self.detail = message

    def at(self, layer: str) -> ProfileWidening:
        return ProfileWidening(self.field, self.detail, layer=layer)


class ProfileInvalid(EngineRefusal):
    """A profile layer that cannot be read as a narrowing; never treated as absent."""

    reason_code = "profile_invalid"


class ProfileCeilingUnavailable(EngineRefusal):
    """The org ceiling could not be read; a run never falls back to the release default."""

    reason_code = "profile_ceiling_unavailable"


class SkillNotAllowed(EngineRefusal):
    reason_code = "skill_not_allowed"


class EgressUnavailable(EngineRefusal):
    """The profile names network beyond the gateways, which no sandbox here provides."""

    reason_code = "egress_unavailable"


class GrantTooLarge(EngineRefusal):
    reason_code = "grant_too_large"

    def __init__(self, tools: int, max_tools: int) -> None:
        super().__init__(f"grant of {tools} tools exceeds the engine limit of {max_tools}")
        self.tools = tools
        self.max_tools = max_tools


class ModelApiUnavailable(EngineRefusal):
    """The engine speaks no API the deployment's model gateway serves."""

    reason_code = "model_api_unavailable"


class ToolNameTooLong(EngineRefusal):
    """A granted tool's name is longer than the engine can carry to the model; never truncated."""

    reason_code = "tool_name_too_long"

    def __init__(self, longest: int, max_length: int) -> None:
        super().__init__(f"a granted tool name of {longest} characters exceeds the engine limit of {max_length}")
        self.longest = longest
        self.max_length = max_length


class SteeringUnsupported(EngineRefusal):
    reason_code = "steering_unsupported"


class CompactUnsupported(EngineRefusal):
    reason_code = "compact_unsupported"


class CompactFailed(EngineRefusal):
    """The engine was asked to compact (``/compact``) and did not finish: the turn goes on uncompacted."""

    reason_code = "compact_failed"


class EngineSessionClosed(EngineRefusal):
    reason_code = "session_closed"


class EngineStateUnavailable(EngineRefusal):
    """Saved engine state cannot be read back; ``reason_code`` is ``state_missing`` or ``state_corrupt``."""

    reason_code = "state_unavailable"


class EngineImageNotConfigured(EngineRefusal):
    """No sandbox image is configured for an allow-listed engine version."""

    reason_code = "engine_image_missing"


class EngineInputUnavailable(EngineRefusal):
    """An input artifact the run cannot take: missing, discarded, of another conversation, without that
    version, or not readable by the run's actor. One code for all, so a refusal reveals no artifact.

    ``name``: the file's name, only when the person already knows it (an artifact of the run's own conversation that
    has since been deleted or lost that version); never for one the actor may not read."""

    reason_code = "engine_input_unavailable"

    def __init__(self, message: str, *, reason: str | None = None, name: str | None = None) -> None:
        super().__init__(message, reason=reason)
        self.name = name


class EngineInputConflict(EngineRefusal):
    """Two input artifacts would be the same file under ``in/``."""

    reason_code = "engine_input_conflict"


class EngineInputTooLarge(EngineRefusal):
    """An input artifact over the per-input byte cap (C3.08): refused before any of it is staged. The message
    names the file and the limit, in words the person reads."""

    reason_code = "engine_input_too_large"

    def __init__(self, name: str, size_bytes: int, max_bytes: int) -> None:
        super().__init__(f"{name} is too large to give to a task ({_size(size_bytes)}; the limit is {_size(max_bytes)})")
        self.name = name
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes

    def why(self) -> str:
        """Why the file was not given, in words that follow its name ("plan.csv: ...")."""
        return f"it is too large to give to a task: {_size(self.size_bytes)}, and the limit is {_size(self.max_bytes)}"


def _size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} bytes"


class EngineInputsUnsupported(EngineRefusal):
    """The run's input names artifacts and this host binds no input source: never run without them."""

    reason_code = "engine_inputs_unsupported"


class SnapshotRefused(EngineRefusal):
    """A sandbox snapshot that is not taken or not restored (P5.9b); ``reason_code`` names why
    (``snapshot_secret``, ``snapshot_too_large``, ``snapshot_unsafe``, ``snapshot_sandbox_gone``, ``snapshot_failed``)."""

    reason_code = "snapshot_refused"


class SnapshotUnavailable(EngineRefusal):
    """A stored sandbox snapshot cannot be read back: ``snapshot_missing``, ``snapshot_corrupt`` or
    ``snapshot_foreign`` (another session's)."""

    reason_code = "snapshot_unavailable"


class EngineImageUnavailable(EngineRefusal):
    """The engine's image is configured but its sandbox broker can neither find nor pull it (an admin-supplied
    image that was never built here, D29): refused before a sandbox is asked for."""

    reason_code = "engine_unavailable"


class EngineContractUnsupported(EngineRefusal):
    """The engine image declares a ``/pipeshub`` contract version this worker does not write, or none (WS-12):
    its ``ph`` and folder layout would not match what the turn seeds, so the turn is refused before the engine runs."""

    reason_code = "engine_contract_unsupported"


class EngineImageChanged(EngineRefusal):
    """The run is pinned to an engine image (its digest, its ``/pipeshub`` contract) that is no longer the one this
    deployment runs (C2.07, C2.20): the image was rebuilt after the run was admitted. Refused before the engine
    starts, never run on an image the run was not admitted under; a new message pins the new image."""

    reason_code = "engine_image_changed"


class StateHoldsSecret(ValueError):
    """Captured engine state contains a credential; it must never be saved."""


class EngineGone(Exception):
    """The engine process died or never came up. Not a refusal: the turn is lost and a later attempt resumes it,
    so the kernel releases the attempt with ``reason_code`` (P9 FB-5)."""

    reason_code: str = "engine_gone"


class EngineUnhealthy(EngineGone):
    """The engine never answered its health check or handshake after it was started."""

    reason_code = "engine_unhealthy"


class EngineCrashed(EngineGone):
    """The engine's process ended under the adapter: its stream closed, or a command it ran was killed."""

    reason_code = "engine_crashed"


def killed_by_signal(exit_code: int) -> bool:
    """A process killed by a signal: a shell (and ``docker exec``) reports ``128 + n``, a direct child ``-n``."""
    return exit_code < 0 or exit_code > 128


__all__ = [
    "CompactFailed",
    "CompactUnsupported",
    "EgressUnavailable",
    "EngineBinaryMismatch",
    "EngineContractUnsupported",
    "EngineCrashed",
    "EngineGone",
    "EngineImageChanged",
    "EngineRefusal",
    "EngineImageNotConfigured",
    "EngineImageUnavailable",
    "EngineInputConflict",
    "EngineInputTooLarge",
    "EngineInputUnavailable",
    "EngineInputsUnsupported",
    "EngineSessionClosed",
    "EngineStateUnavailable",
    "EngineStateIncompatible",
    "EngineUnhealthy",
    "EngineVersionNotAllowed",
    "GrantTooLarge",
    "ModelApiUnavailable",
    "ProfileCeilingUnavailable",
    "ProfileInvalid",
    "ProfileWidening",
    "SkillNotAllowed",
    "SnapshotRefused",
    "SnapshotUnavailable",
    "StateHoldsSecret",
    "SteeringUnsupported",
    "ToolNameTooLong",
    "killed_by_signal",
]
