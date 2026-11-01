"""redact_secrets_in_text and the formatters and handlers that apply it."""

import ast
import io
import logging
import logging.config
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.utils import log_redaction
from app.utils.log_redaction import (
    REDACTED,
    log_safe_validation_errors,
    redact_secrets_in_text,
)
from app.utils.logger import (
    ColoredFormatter,
    RedactingFormatter,
    create_logger,
    install_log_redaction,
)
from app.utils.sensitive_names import is_credential_name

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0SENTINELpem\nabcdef\n"
    "-----END RSA PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer sentinel-bearer-token", "sentinel-bearer-token"),
        ("sending bearer sentinel-bearer-token now", "sentinel-bearer-token"),
        ("Basic dXNlcjpzZW50aW5lbA== rejected", "dXNlcjpzZW50aW5lbA=="),
        (f"token is {JWT}", JWT),
        ("pat phpat_SENTINELabcdefgh1234", "phpat_SENTINELabcdefgh1234"),
        ("key sk-proj-SENTINELabcdefghijklmnop", "sk-proj-SENTINELabcdefghijklmnop"),
        ("key sk-ant-api03-SENTINELabcdefghijk", "sk-ant-api03-SENTINELabcdefghijk"),
        ("aws AKIAIOSFODNN7SENTINL", "AKIAIOSFODNN7SENTINL"),
        ("file report_sk-SENTINELabcdefghijklmnop.txt", "sk-SENTINELabcdefghijklmnop"),
        ("file report-sk-SENTINELabcdefghijklmnop.txt", "sk-SENTINELabcdefghijklmnop"),
        ("file report.sk-SENTINELabcdefghijklmnop.txt", "sk-SENTINELabcdefghijklmnop"),
        ("file backup_AKIAIOSFODNN7SENTINL.csv", "AKIAIOSFODNN7SENTINL"),
        ("password=alice_sk-2026-account-production-report:SENTINEL-pw2@host", "SENTINEL-pw2"),
        ("file out_ghp_SENTINEL0123456789abcdefghijklmnopqrstuvwx", "ghp_SENTINEL0123456789abcdefghijklmnopqrstuvwx"),
        ("slack xoxb-123456789012-SENTINELabc", "xoxb-123456789012-SENTINELabc"),
        ("google AIzaSyA-SENTINEL_0123456789abcdefghijk", "AIzaSyA-SENTINEL_0123456789abcdefghijk"),
        ("gh ghp_SENTINEL0123456789abcdefghijklmnopqrstuvwx", "ghp_SENTINEL0123456789abcdefghijklmnopqrstuvwx"),
        (f"key material {PEM} done", "SENTINELpem"),
        ("connecting to postgresql://app:SENTINELpw@db:5432/x", "SENTINELpw"),
        ("GET https://h.example.com/v1/models?key=SENTINELkey&alt=json", "SENTINELkey"),
        ("redirect 'https://h.example.com/dl?token=SENTINELtok'", "SENTINELtok"),
        ('cfg {"clientSecret": "SENTINEL-cs", "clientId": "abc"}', "SENTINEL-cs"),
        ("cfg {'password': 'SENTINEL-pw', 'user': 'bob'}", "SENTINEL-pw"),
        ("cfg {'auth': {'patToken': 'SENTINEL-pat'}}", "SENTINEL-pat"),
        ("cfg {'privateKey': 'SENTINEL-pk'}", "SENTINEL-pk"),
        ("apiKey=SENTINEL-ak other=1", "SENTINEL-ak"),
        ("Error: password=SENTINEL-nested", "SENTINEL-nested"),
        ('escaped {"password": "a\\"SENTINEL-esc"}', "SENTINEL-esc"),
        ("truncated {'password': 'SENTINEL-cut", "SENTINEL-cut"),
        ("bytes password: b'SENTINEL-bytes'", "SENTINEL-bytes"),
        ("dsn connection_string='Server=x;Password=SENTINEL-cs2'", "SENTINEL-cs2"),
        (
            "connection_string=DefaultEndpointsProtocol=https;AccountName=acct;"
            "AccountKey=SENTINELazkey0123==;EndpointSuffix=core.windows.net",
            "SENTINELazkey0123",
        ),
        (
            "conn Endpoint=sb://ns.servicebus.windows.net/;SharedAccessKeyName=Root;"
            "SharedAccessKey=SENTINELsbkey0123=",
            "SENTINELsbkey0123",
        ),
        (
            "BlobEndpoint=https://a.blob.core.windows.net/;"
            "SharedAccessSignature=sv=2022-11-02&ss=b&sp=rl&sig=SENTINELsas%2Fsig",
            "SENTINELsas",
        ),
        (
            "1 validation error for Login\npassword\n  String should have at most 4 characters "
            "[type=string_too_long, input_value='SENTINEL-iv, x]', input_type=str]",
            "SENTINEL-iv",
        ),
        ("cut off ... [type=missing, input_value={'pwd2': 'SENTINEL-ivcut", "SENTINEL-ivcut"),
    ],
)
def test_secret_is_masked(text: str, secret: str) -> None:
    redacted = redact_secrets_in_text(text)

    assert secret not in redacted
    assert REDACTED in redacted or "REDACTED" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "Getting key: /services/oauth/gmail",
        "📝 set_config called for key: /services/connectors/3fa85f64-5717-4562-b3fc-2c963f66afa6/config",
        "OAuth config 3fa85f64-5717-4562-b3fc-2c963f66afa6 not found",
        "chunking each passage by keyword with basic settings",
        "Using Basic auth",
        "token_limit=4096 max_tokens: 8000 prompt_tokens=12",
        "InvalidTokenError: token has expired",
        '  File "/app/python/app/config/configuration_service.py", line 97, in get_config',
        "2026-10-18 16:43:27,879 - etcd - DEBUG - [etcd3_store.py:395] - Getting key from ETCD: /services/endpoints",
        "task-runner-abcdefghijklmnopqrstuvwxyz finished on disk-cache",
        "risk-assessment-for-the-quarterly-review.pdf and desk-booking-report-final-v2.xlsx",
        "ask-the-team-about-the-migration-plan_v3 ask_sk-",
        "GET https://h.example.com/api/v1/records?page=2&limit=50 200",
        "password=",
        "secret: [REDACTED]",
        "AccountName=acct account_id=4242424242 accountIdentifier=xy12345.eu-west-1 AccountKind=StorageV2",
    ],
)
def test_ordinary_text_is_unchanged(text: str) -> None:
    assert redact_secrets_in_text(text) == text


class TestEnvSecrets:
    @pytest.fixture(autouse=True)
    def _restore(self) -> Iterator[None]:
        yield
        log_redaction.refresh_env_secrets()

    def test_secret_env_values_are_masked_anywhere(self, monkeypatch) -> None:
        monkeypatch.setenv("SECRET_KEY", "SENTINEL-env-secret-key")
        monkeypatch.setenv("REDIS_PASSWORD", "SENTINELredis")
        monkeypatch.setenv("QDRANT_API_KEY", "SENTINELqdrant-api")
        log_redaction.refresh_env_secrets()

        redacted = redact_secrets_in_text(
            "redis error at redis://:SENTINELredis@x, key SENTINEL-env-secret-key, SENTINELqdrant-api"
        )

        assert "SENTINEL" not in redacted

    def test_short_and_non_secret_env_values_are_left(self, monkeypatch) -> None:
        monkeypatch.setenv("ETCD_PASSWORD", "short")
        monkeypatch.setenv("ACCESS_TOKEN_EXPIRY", "86400000s")
        monkeypatch.setenv("OAUTH_TOKEN_URL", "https://oauth.example.com/token")
        monkeypatch.setenv("HIDE_SECRET_CONFIG", "true")
        log_redaction.refresh_env_secrets()

        text = "short 86400000s https://oauth.example.com/token true"
        assert redact_secrets_in_text(text) == text


class TestLinearTime:
    """Each shape below makes a backtracking pattern quadratic or worse.

    Wall-clock limits break under coverage or a loaded xdist worker, so each
    shape is timed at two sizes instead: linear work grows about 4x from n to
    4n, quadratic work 16x.
    """

    SMALL = 250_000
    LARGE = 4 * SMALL
    # Below 8x, so a quadratic pass (16x) fails while timer noise on a linear one passes.
    MAX_GROWTH = 8
    # Absorbs scheduler jitter when both runs take only a few milliseconds;
    # a quadratic pass over a megabyte takes far longer than this.
    SLACK_SECONDS = 0.05

    @staticmethod
    def _repeat(unit: str, size: int) -> str:
        return (unit * (size // len(unit) + 1))[:size]

    @staticmethod
    def _best_of_three(text: str) -> float:
        best = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            redact_secrets_in_text(text)
            best = min(best, time.perf_counter() - start)
        return best

    def _assert_linear(self, make: Callable[[int], str]) -> None:
        small = self._best_of_three(make(self.SMALL))
        large = self._best_of_three(make(self.LARGE))
        assert large < self.MAX_GROWTH * small + self.SLACK_SECONDS, (
            f"{self.SMALL} chars took {small:.4f}s, {self.LARGE} chars {large:.4f}s"
        )

    def test_one_megabyte_single_token(self) -> None:
        text = "a" * self.LARGE
        assert redact_secrets_in_text(text) == text
        self._assert_linear(lambda n: "a" * n)

    @pytest.mark.parametrize(
        "unit",
        [
            "eyJ", "eyJaaaaaaaa.", "_eyJ", "-eyJaaaaaaaa.", "sk-", "_sk-", "_xoxb-", "AKIA", "phpat_", "xoxb-", "AIza", "gh",
            "password=", 'password:"', "password: '", "a:", "a=", "a.", "a" * 200 + ":",
            "-----BEGIN ", "-----BEGIN PRIVATE KEY-----", "Bearer ", "Basic AAAAAAA= ",
            "https://", "://", "https://h/?" + "=" * 900 + " ", "https://u:" + "p@" * 400 + " ",
            " ", "'", '"', "\\", "input_value=", "input_value=x, input_type",
        ],
    )
    def test_pathological_shapes(self, unit: str) -> None:
        self._assert_linear(lambda n: self._repeat(unit, n))

    @pytest.mark.parametrize(
        ("prefix", "fill", "suffix"),
        [
            ("sk-", "a", ""),
            ("Bearer ", "A", ""),
            ('password: "', "x", ""),
            ("", "x", "://"),
            ("https://a/?", "=", ""),
            ("-----BEGIN PRIVATE KEY-----", "A", ""),
            ("input_value=", "x", ""),
        ],
    )
    def test_one_long_run_after_a_trigger(self, prefix: str, fill: str, suffix: str) -> None:
        self._assert_linear(lambda n: prefix + fill * n + suffix)


class TestPydanticValidationErrors:
    class _Login(BaseModel):
        username: str
        password: str = Field(max_length=4)

    def test_str_of_a_validation_error_is_redacted_by_the_formatter(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            self._Login(username="bob", password="SENTINEL-pydantic-pw")
        assert "SENTINEL-pydantic-pw" in str(exc_info.value)

        out = RedactingFormatter("%(message)s").format(_record("invalid: %s", exc_info.value))

        assert "SENTINEL-pydantic-pw" not in out
        assert "password" in out
        assert "string_too_long" in out

    def test_log_safe_errors_keep_where_and_why_but_not_the_input(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            self._Login(username="bob", password="SENTINEL-pydantic-pw")

        safe = log_safe_validation_errors(exc_info.value.errors())

        assert safe == [{
            "type": "string_too_long",
            "loc": ("password",),
            "msg": "String should have at most 4 characters",
            "url": exc_info.value.errors()[0]["url"],
        }]


def _record(msg: str, *args, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("svc", logging.ERROR, __file__, 1, msg, args, exc_info)


class TestFormatters:
    def test_percent_args_are_redacted(self) -> None:
        out = RedactingFormatter("%(message)s").format(_record("password=%s", "SENTINEL-arg"))
        assert out == f"password={REDACTED}"

    def test_traceback_is_redacted(self) -> None:
        try:
            raise RuntimeError("upstream said Authorization: Bearer SENTINEL-tb")
        except RuntimeError:
            record = _record("call failed", exc_info=sys.exc_info())

        out = RedactingFormatter("%(message)s").format(record)

        assert "Traceback" in out
        assert "SENTINEL-tb" not in out

    def test_colored_formatter_redacts_and_still_colors(self) -> None:
        out = ColoredFormatter("%(message)s").format(_record("token: SENTINEL-color"))
        assert out.startswith(ColoredFormatter.RED)
        assert "SENTINEL-color" not in out


class TestInstallation:
    def test_create_logger_handlers_redact(self) -> None:
        logger = create_logger("test_log_redaction_service")
        try:
            assert logger.handlers
            assert all(isinstance(h.formatter, RedactingFormatter) for h in logger.handlers)
        finally:
            for handler in logger.handlers:
                handler.close()
            logger.handlers.clear()

    def test_root_handler_from_basicconfig_redacts(self) -> None:
        # A fresh interpreter: under pytest the root logger already has
        # handlers, so the module's basicConfig() would add none.
        backend = Path(__file__).resolve().parents[3]
        code = (
            "import logging, app.utils.logger; "
            "logging.getLogger('app.x').warning('password=%s', 'SENTINEL-basic')"
        )
        env = {**os.environ, "PYTHONPATH": str(backend)}
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=backend, env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "password=" in result.stderr
        assert "SENTINEL-basic" not in result.stderr

    def test_a_module_logger_reaches_the_root_handler_redacted(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            install_log_redaction()
            logging.getLogger("app.api.routes.parsing").error("apiKey=%s", "SENTINEL-root")
        finally:
            root.removeHandler(handler)
        assert "SENTINEL-root" not in stream.getvalue()
        assert "apiKey=" in stream.getvalue()

    def test_uvicorn_handlers_redact_after_uvicorn_configures_logging(self) -> None:
        from uvicorn.config import LOGGING_CONFIG

        names = ("uvicorn", "uvicorn.error", "uvicorn.access")
        saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).level) for n in names}
        stream = io.StringIO()
        try:
            logging.config.dictConfig(LOGGING_CONFIG)
            for name in ("uvicorn", "uvicorn.access"):
                for handler in logging.getLogger(name).handlers:
                    handler.setStream(stream)

            install_log_redaction()
            install_log_redaction()

            for name in ("uvicorn", "uvicorn.access"):
                handlers = logging.getLogger(name).handlers
                assert handlers
                assert all(getattr(h.formatter, "_pipeshub_redacting", False) for h in handlers)
                assert all(not getattr(getattr(h.formatter, "inner", None), "_pipeshub_redacting", False) for h in handlers)
            logging.getLogger("uvicorn.error").error("bad header Authorization: Bearer SENTINEL-uv")
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", "/x?api_key=SENTINEL-acc", "1.1", 200
            )
        finally:
            for name, (handlers, level) in saved.items():
                logger = logging.getLogger(name)
                logger.handlers[:] = handlers
                logger.setLevel(level)
        out = stream.getvalue()
        assert "GET /x" in out
        assert "SENTINEL" not in out


def test_every_service_installs_redaction_at_startup() -> None:
    """uvicorn replaces its handlers after app.utils.logger is imported, so
    each service's lifespan must install redaction again."""
    app_dir = Path(__file__).resolve().parents[3] / "app"
    mains = sorted(p for p in app_dir.glob("*_main.py") if "uvicorn.run" in p.read_text())
    assert len(mains) >= 7
    for path in mains:
        tree = ast.parse(path.read_text())
        lifespans = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
        assert lifespans, path.name
        calls = {
            c.func.id
            for c in ast.walk(lifespans[0])
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        }
        assert "install_log_redaction" in calls, path.name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("AccountKey", True),
        ("account_key", True),
        ("SharedAccessKey", True),
        ("SharedAccessSignature", True),
        ("AccountName", False),
        ("account_id", False),
        ("accountIdentifier", False),
        ("AccountKind", False),
    ],
)
def test_account_key_is_a_credential_name(name: str, expected: bool) -> None:
    assert is_credential_name(name) is expected
