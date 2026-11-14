"""Tests for app.agent_loop_lib.sandbox.coding.validation.

The canonical-matching helpers exist because of a production incident: the
PRE_TOOL_USE package policy (`app/sandbox/package_policy.py`) canonicalizes
names before its allowlist lookup, but the backend-level check inside
`EnvironmentManager.install_packages` compared raw names — so a spec like
`Pillow` passed the hook, then failed instantly inside `run_code` with
"package 'Pillow' is not in the configured allowlist", and the model gave
up on the file-generation task. Both layers must accept exactly the same
specs.
"""

from __future__ import annotations

from app.agent_loop_lib.sandbox.coding.validation import (
    canonical_package_key,
    matches_package_set,
    package_name,
    validate_package_spec,
)


class TestCanonicalPackageKey:
    def test_python_lowercases(self) -> None:
        assert canonical_package_key("Pillow", "python") == "pillow"

    def test_python_underscore_to_hyphen(self) -> None:
        assert canonical_package_key("python_docx", "python") == "python-docx"

    def test_npm_lowercases_but_keeps_underscores(self) -> None:
        assert canonical_package_key("My_Pkg", "typescript") == "my_pkg"


class TestMatchesPackageSet:
    ALLOWLIST = {"pillow", "python-docx", "reportlab", "fpdf2", "jinja2"}

    def test_exact_match(self) -> None:
        assert matches_package_set("reportlab", self.ALLOWLIST, "python")

    def test_case_insensitive_match(self) -> None:
        assert matches_package_set("Pillow", self.ALLOWLIST, "python")
        assert matches_package_set("Jinja2", self.ALLOWLIST, "python")

    def test_underscore_variant_matches(self) -> None:
        assert matches_package_set("python_docx", self.ALLOWLIST, "python")

    def test_non_canonical_allowlist_entries_match_canonical_name(self) -> None:
        assert matches_package_set("pillow", {"Pillow"}, "python")

    def test_unknown_package_does_not_match(self) -> None:
        assert not matches_package_set("requests", self.ALLOWLIST, "python")

    def test_npm_underscore_is_not_collapsed(self) -> None:
        # npm treats `a_b` and `a-b` as different packages.
        assert not matches_package_set("fs_extra", {"fs-extra"}, "typescript")


class TestVersionedSpecs:
    def test_pypi_version_spec_extracts_name(self) -> None:
        assert package_name("reportlab==4.2.5", "python") == "reportlab"
        assert validate_package_spec("reportlab==4.2.5", "python")

    def test_npm_version_spec_extracts_name(self) -> None:
        assert package_name("lodash@4.17.21", "typescript") == "lodash"

    def test_versioned_capitalized_spec_matches_allowlist(self) -> None:
        name = package_name("Pillow==10.4.0", "python")
        assert matches_package_set(name, {"pillow"}, "python")


class TestExactPinsOnly:
    """Only a bare name or an exact version installs: a range or a tag lets
    the registry hand over whatever release is newest at install time."""

    def test_pypi_bare_and_exact_pins_accepted(self) -> None:
        for spec in ["pandas", "Pillow", "python_docx", "numpy==1.26.0", "numpy==2.0", "pydantic==2.0.0rc1"]:
            assert validate_package_spec(spec, "python"), spec

    def test_pypi_ranges_and_wildcards_refused(self) -> None:
        for spec in [
            "numpy>=1.26", "numpy<2", "numpy~=1.26", "numpy!=1.0", "numpy>1",
            "numpy==1.26.*", "numpy===1.26.0", "numpy>=1,<2", "numpy==", "numpy=1.0",
        ]:
            assert not validate_package_spec(spec, "python"), spec

    def test_npm_bare_and_exact_pins_accepted(self) -> None:
        for spec in ["lodash", "lodash@4.17.21", "@types/node", "@types/node@20.11.5", "pkg@1.0.0-beta.1"]:
            assert validate_package_spec(spec, "typescript"), spec

    def test_npm_ranges_and_tags_refused(self) -> None:
        for spec in [
            "lodash@^4.17.21", "lodash@~4.17.21", "lodash@*", "lodash@4", "lodash@4.17",
            "lodash@4.x", "lodash@latest", "lodash@next", "@types/node@^20.0.0", "@types/node@latest",
        ]:
            assert not validate_package_spec(spec, "typescript"), spec

    def test_url_git_file_and_flags_still_refused(self) -> None:
        for spec in ["git+https://x/y.git", "https://x/y.tgz", "file:../y", "-e", "--index-url=x", "a;b"]:
            assert not validate_package_spec(spec, "python"), spec
            assert not validate_package_spec(spec, "typescript"), spec


class TestEnvironmentManagerAllowlist:
    """The backend layer itself must accept the same spelling variants."""

    def _manager(self, tmp_path, allowlist: list[str]):
        from unittest.mock import AsyncMock

        from app.agent_loop_lib.sandbox.base import ExecResult
        from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager

        manager = EnvironmentManager(str(tmp_path), package_allowlist=allowlist)
        manager.ensure_python_venv = AsyncMock()
        manager._run_confined = AsyncMock(
            return_value=ExecResult(stdout="", stderr="", exit_code=0)
        )
        return manager

    async def test_capitalized_spec_passes_backend_allowlist(self, tmp_path) -> None:
        manager = self._manager(tmp_path, ["pillow", "reportlab"])
        result = await manager.install_packages(["Pillow"], "python")
        assert result.success, result.stderr

    async def test_second_install_with_different_casing_is_skipped(self, tmp_path) -> None:
        manager = self._manager(tmp_path, ["pillow"])
        first = await manager.install_packages(["Pillow"], "python")
        assert first.success and first.installed
        second = await manager.install_packages(["pillow"], "python")
        assert second.success
        assert second.installed == []

    async def test_denylist_matches_canonically(self, tmp_path) -> None:
        from unittest.mock import AsyncMock

        from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager

        manager = EnvironmentManager(str(tmp_path), package_denylist=["evil-pkg"])
        manager.ensure_python_venv = AsyncMock()
        result = await manager.install_packages(["Evil_Pkg"], "python")
        assert not result.success
        assert "denylisted" in result.stderr


class TestHookBackendConsistency:
    """Any spec the PipesHub PRE_TOOL_USE policy admits must also pass the
    backend-level check performed with the combined curated allowlist —
    otherwise run_code fails after the hook already approved the call."""

    def test_curated_allowlist_accepts_hook_approved_variants(self) -> None:
        from app.agents.agent_loop.sandbox_bridge import _curated_package_allowlist
        from app.sandbox.models import SandboxLanguage
        from app.sandbox.package_policy import enforce_package_allowlist

        combined = set(_curated_package_allowlist())
        for spec in ["Pillow", "ReportLab", "python_docx", "Jinja2", "pandas==2.2.3", "FPDF2"]:
            enforce_package_allowlist([spec], SandboxLanguage.PYTHON)  # hook layer: must not raise
            name = package_name(spec, "python")
            assert matches_package_set(name, combined, "python"), (
                f"{spec!r} passes the PRE_TOOL_USE policy but would be rejected "
                f"by the backend allowlist check"
            )


class TestInstallCommandFlags:
    """pip installs wheels only (an sdist runs its build script on install)
    and npm never runs lifecycle scripts."""

    async def test_environment_manager_pip_is_binary_only(self, tmp_path) -> None:
        from unittest.mock import AsyncMock

        from app.agent_loop_lib.sandbox.base import ExecResult
        from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager

        manager = EnvironmentManager(str(tmp_path))
        manager.ensure_python_venv = AsyncMock()
        manager._run_confined = AsyncMock(return_value=ExecResult(stdout="", stderr="", exit_code=0))
        assert (await manager.install_packages(["six==1.17.0"], "python")).success
        cmd = manager._run_confined.call_args.args[0]
        assert cmd[1:] == ["install", "--no-cache-dir", "--only-binary=:all:", "six==1.17.0"]

    async def test_environment_manager_npm_ignores_scripts(self, tmp_path) -> None:
        from unittest.mock import AsyncMock

        from app.agent_loop_lib.sandbox.base import ExecResult
        from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager

        manager = EnvironmentManager(str(tmp_path))
        manager.ensure_typescript_runtime = AsyncMock()
        manager._run_confined = AsyncMock(return_value=ExecResult(stdout="", stderr="", exit_code=0))
        assert (await manager.install_packages(["lodash@4.17.21"], "typescript")).success
        cmd = manager._run_confined.call_args.args[0]
        assert cmd[:3] == ["npm", "install", "--ignore-scripts"]

    async def test_e2b_install_commands(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        sandbox = E2BCodingSandbox()
        sandbox._sbx = MagicMock()
        sandbox._ensure_node_project = AsyncMock()
        sandbox._run_command = AsyncMock(return_value=("", "", 0))
        assert (await sandbox.install_packages(["six==1.17.0"], "python")).success
        assert sandbox._run_command.call_args.args[0] == (
            "pip install --no-cache-dir --only-binary=:all: six==1.17.0"
        )
        assert (await sandbox.install_packages(["lodash@4.17.21"], "typescript")).success
        assert sandbox._run_command.call_args.args[0].startswith("npm install --ignore-scripts ")
