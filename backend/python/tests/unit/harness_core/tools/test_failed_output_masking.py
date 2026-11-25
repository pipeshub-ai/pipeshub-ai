"""A failed tool output is masked by the host's redactor through the log-lines binding, and without a host by the
kernel's own token patterns: harness_core may not import the app, so the masker is a seam."""

from __future__ import annotations

from harness_core import bindings
from harness_core.contracts.vectors.token_patterns import SECRET_MASK
from harness_core.log_lines import NeutralLogLines
from harness_core.tools.base import ToolOutput, error_data
from harness_core.tools.executor import _without_secrets

GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


class _HostLines(NeutralLogLines):
    def redact_secrets_in_text(self, text: str) -> str:
        return text.replace("hunter2", "[HOST]")

    def credential_value_mask(self, name: str) -> str | None:
        return "[HOST]" if name == "pin" else None


def test_without_a_host_the_kernel_masks_token_shapes_and_secret_named_fields() -> None:
    with bindings.scoped(bindings.NEUTRAL):
        out = _without_secrets(
            ToolOutput(success=False, error=f"401 for {GITHUB_TOKEN}", data={"password": "x", "retries": 3})
        )
        assert out.error == f"401 for {SECRET_MASK}"
        assert out.data == {"password": SECRET_MASK, "retries": 3}
        assert error_data(ValueError(GITHUB_TOKEN)) == {"error": SECRET_MASK}


def test_the_host_binding_decides_the_mask() -> None:
    with bindings.scoped(bindings.KernelBindings(log_lines=_HostLines())):
        out = _without_secrets(ToolOutput(success=False, error="pw hunter2", data={"pin": "1234", "ok": True}))
        assert out.error == "pw [HOST]"
        assert out.data == {"pin": "[HOST]", "ok": True}


def test_a_successful_output_is_left_as_the_tool_wrote_it() -> None:
    ok = ToolOutput(success=True, data={"password": "x"})
    assert _without_secrets(ok) is ok
