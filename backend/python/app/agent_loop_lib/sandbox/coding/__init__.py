from typing import TYPE_CHECKING

from harness_core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from app.agent_loop_lib.sandbox.coding.base import (
        CodeRequest,
        CodeResult,
        CodingLanguage,
        CodingSandboxBackend,
        CodingSandboxError,
        EnvironmentSetupError,
        ErrorAnalysis,
        ErrorCategory,
        ExecutionEvent,
        InstallResult,
        IsolationLevel,
        SandboxCapabilities,
        SandboxContext,
        SandboxRef,
    )
    from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
    from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox
    from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
    from app.agent_loop_lib.sandbox.coding.executor import CodeExecutor, ExecutionLimits
    from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
    from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine
    from app.agent_loop_lib.sandbox.coding.registry import (
        BackendHealth,
        SandboxBackendFactory,
        SandboxBackendRegistry,
        build_default_registry,
    )
    from app.agent_loop_lib.sandbox.coding.validation import (
        package_name,
        validate_package_spec,
    )

# Each name's submodule, imported when the name is first read (R25OPS-FU-1).
_EXPORTS = {
    "CodeRequest": "base",
    "CodeResult": "base",
    "CodingLanguage": "base",
    "CodingSandboxBackend": "base",
    "CodingSandboxError": "base",
    "EnvironmentSetupError": "base",
    "ErrorAnalysis": "base",
    "ErrorCategory": "base",
    "ExecutionEvent": "base",
    "InstallResult": "base",
    "IsolationLevel": "base",
    "SandboxCapabilities": "base",
    "SandboxContext": "base",
    "SandboxRef": "base",
    "DockerCodingSandbox": "docker",
    "E2BCodingSandbox": "e2b",
    "EnvironmentManager": "environment",
    "CodeExecutor": "executor",
    "ExecutionLimits": "executor",
    "LocalCodingSandbox": "local",
    "ReflectionEngine": "reflection",
    "BackendHealth": "registry",
    "SandboxBackendFactory": "registry",
    "SandboxBackendRegistry": "registry",
    "build_default_registry": "registry",
    "package_name": "validation",
    "validate_package_spec": "validation",
}
__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)

__all__ = [
    "CodeRequest",
    "CodeResult",
    "CodingLanguage",
    "CodingSandboxBackend",
    "CodingSandboxError",
    "EnvironmentSetupError",
    "ErrorAnalysis",
    "ErrorCategory",
    "ExecutionEvent",
    "InstallResult",
    "IsolationLevel",
    "SandboxCapabilities",
    "SandboxContext",
    "SandboxRef",
    "EnvironmentManager",
    "CodeExecutor",
    "ExecutionLimits",
    "LocalCodingSandbox",
    "E2BCodingSandbox",
    "DockerCodingSandbox",
    "ReflectionEngine",
    "BackendHealth",
    "SandboxBackendFactory",
    "SandboxBackendRegistry",
    "build_default_registry",
    "validate_package_spec",
    "package_name",
]
