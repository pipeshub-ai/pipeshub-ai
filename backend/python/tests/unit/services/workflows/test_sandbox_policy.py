"""Sandbox policy: the import allowlist and the builtins the sandbox execs under.

Both layers are tested separately on purpose. The verifier is what an author
hits at commit time, but a version row written by any other path reaches the
exec unverified, so `build_safe_builtins()` has to refuse the same code on its
own.
"""
from __future__ import annotations

import pytest

from app.services.workflows.codegen.verifier import verify_workflow_source
from app.services.workflows.security.sandbox_policy import (
    build_safe_builtins,
    is_module_allowed,
)

_ESCAPE_CODES = {"BANNED_IMPORT", "BANNED_BUILTIN", "BANNED_ATTRIBUTE"}

# The shape codegen emits: `__future__`, an allowed stdlib module, the SDK.
VALID_SOURCE = '''
from __future__ import annotations

import json
from datetime import datetime

from app.services.workflows.sdk import Ctx, workflow


@workflow(name="ok")
async def ok(ctx: Ctx, payload: dict) -> dict:
    result = await ctx.tool("slack.send_message", channel="C1")
    seen = datetime.fromisoformat(payload["at"])
    return {"ok": True, "raw": json.dumps(result), "year": seen.year}
'''


def _escape_errors(source: str) -> set[str]:
    return {e.code for e in verify_workflow_source(source).errors} & _ESCAPE_CODES


class TestImportAllowlist:
    def test_generated_workflow_shape_passes(self) -> None:
        assert verify_workflow_source(VALID_SOURCE).ok

    @pytest.mark.parametrize(
        ("label", "source"),
        [
            # Every one of these passed the old denylist.
            ("os", "import os\nos.system('id')"),
            ("os_submodule", "import os.path"),
            ("os_alias", "import os as _o"),
            ("os_from", "from os import system"),
            ("sys", "import sys"),
            ("io", "import io"),
            ("pathlib", "from pathlib import Path"),
            ("builtins", "import builtins"),
            ("posix", "import posix"),
            ("importlib", "import importlib"),
            # Allowing the `app` root would hand workflow code the etcd client.
            ("app_config", "from app.config.configuration_service import ConfigurationService"),
            # No package to resolve against, so `node.module` alone reads as allowed.
            ("relative", "from . import helper"),
        ],
    )
    def test_capability_bearing_import_is_rejected(self, label: str, source: str) -> None:
        assert "BANNED_IMPORT" in _escape_errors(source), label

    def test_reflection_escape_needs_no_import(self) -> None:
        # The allowlist cannot see this one; ESCAPE_ATTRIBUTES is what catches it.
        assert "BANNED_ATTRIBUTE" in _escape_errors(
            "x = ().__class__.__bases__[0].__subclasses__()",
        )

    @pytest.mark.parametrize("call", ["getattr(o, 'x')", "eval('1')", "exec('x=1')", "open('/etc/passwd')"])
    def test_reflection_builtins_are_rejected(self, call: str) -> None:
        assert "BANNED_BUILTIN" in _escape_errors(f"x = {call}")

    def test_determinism_lint_still_owns_datetime_calls(self) -> None:
        # `datetime` is importable so parsing works; the *call* is what's wrong.
        errors = verify_workflow_source(
            "from datetime import datetime\nx = datetime.now()",
        ).errors
        assert any(e.code == "RAW_CLOCK" for e in errors)
        assert not any(e.code == "BANNED_IMPORT" for e in errors)

    def test_sdk_prefix_matched_on_full_path(self) -> None:
        assert is_module_allowed("app.services.workflows.sdk")
        assert is_module_allowed("app.services.workflows.sdk.context")
        assert not is_module_allowed("app.services.workflows.security")
        assert not is_module_allowed("app")


class TestSafeBuiltins:
    """Runtime enforcement, with the verifier assumed bypassed."""

    @staticmethod
    def _exec(source: str) -> dict:
        ns: dict = {"__builtins__": build_safe_builtins(), "__name__": "__main__"}
        exec(compile(source, "<workflow>", "exec"), ns)
        return ns

    @pytest.mark.parametrize(
        "source",
        [
            "import os",
            "from os import system",
            "import app.config.configuration_service",
            "__import__('os')",
        ],
    )
    def test_disallowed_import_raises_at_exec(self, source: str) -> None:
        with pytest.raises(ImportError):
            self._exec(source)

    @pytest.mark.parametrize("name", ["getattr", "setattr", "open", "eval", "exec", "globals", "vars"])
    def test_reflection_builtins_absent(self, name: str) -> None:
        assert name not in build_safe_builtins()

    def test_allowed_imports_still_work(self) -> None:
        # The harness execs source that imports the SDK by name, so a blanket
        # `__import__` removal would break every sandbox run.
        ns = self._exec(
            "import json\n"
            "from app.services.workflows.sdk import workflow\n"
            "out = json.dumps({'a': 1})",
        )
        assert ns["out"] == '{"a": 1}'
        assert ns["workflow"] is not None

    def test_builtins_is_a_plain_dict(self) -> None:
        # A module object would let exec'd code reach unlisted names by
        # attribute access.
        assert isinstance(build_safe_builtins(), dict)
