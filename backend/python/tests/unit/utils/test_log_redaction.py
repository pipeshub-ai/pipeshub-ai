"""redact_secrets_in_text and the formatters and handlers that apply it."""

import ast
import io
import json
import logging
import logging.config
import os
import subprocess
import sys
import time
import traceback
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
    JsonFormatter,
    RedactingFormatter,
    TextFormatter,
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
    4n, quadratic work 16x. Runs are timed in this thread's CPU time, so a run
    that waits for a CPU taken by other workers does not count as slower.
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
    def _best_of_three(text: str, redact: Callable[[str], str]) -> float:
        best = float("inf")
        for _ in range(3):
            start = time.thread_time()
            redact(text)
            best = min(best, time.thread_time() - start)
        return best

    def _assert_linear(self, make: Callable[[int], str], redact: Callable[[str], str] = redact_secrets_in_text) -> None:
        small = self._best_of_three(make(self.SMALL), redact)
        large = self._best_of_three(make(self.LARGE), redact)
        assert large < self.MAX_GROWTH * small + self.SLACK_SECONDS, (
            f"{self.SMALL} chars took {small:.4f}s, {self.LARGE} chars {large:.4f}s"
        )

    def test_one_megabyte_single_token(self) -> None:
        text = "a" * self.LARGE
        assert redact_secrets_in_text(text) == text
        self._assert_linear(lambda n: "a" * n)

    @pytest.mark.parametrize(
        ("prefix", "unit"),
        [("", "org-"), ("", "org-a"), ("", "org_"), ("", "proj-a_b-"), ("", "org-aaaa-org_("), ("org-", "a")],
    )
    def test_account_id_shapes_in_error_text(self, prefix: str, unit: str) -> None:
        self._assert_linear(lambda n: prefix + self._repeat(unit, n), log_redaction.redact_error_for_log)

    @pytest.mark.parametrize(
        "unit",
        [
            "eyJ", "eyJaaaaaaaa.", "_eyJ", "-eyJaaaaaaaa.", "sk-", "_sk-", "_xoxb-", "AKIA", "phpat_", "xoxb-", "AIza", "gh",
            "password=", 'password:"', "password: '", "a:", "a=", "a.", "a" * 200 + ":",
            "-----BEGIN ", "-----BEGIN PRIVATE KEY-----", "Bearer ", "Basic AAAAAAA= ",
            "https://", "://", "https://h/?" + "=" * 900 + " ", "https://u:" + "p@" * 400 + " ",
            " ", "'", '"', "\\", "input_value=", "input_value=x, input_type",
            'File "', '  File "a", line 1, in f\n    token = x\n', '  File "a", line 1, in f\n', " |", "|" * 200 + "\n",
            # Kept values (presence words, times, OAuth endpoint URLs); the URL units hold heads that would keep URLs too.
            "token_url=https://", "tokenUrl:https://a/", "token_url:https://", "token_url='https://", "token_url='https://a' ",
            "token_at=1 token_url=https://b ", "token_url=https://h/token_expires_at:1/", "token_url='https://a/tokenUrl:'",
            "refresh_token=None", "has_token=true,", "token_expires_at=1:T", "exp_" * 30 + "token=1234567 ",
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
            ('token\n  File "a", line ', "1", "\n"),
            ('token\n  File "a", line ', "1", ""),
            ('token\n  File "a", line 1, in ', "f", "\n    x"),
            ('  File "a", line 1, in f\n    ', "token=", ""),
            ('  File "a", line 1, in f\n    ', "a=token=", ""),
            ("token_expires_at=", "1", ""),
            ("token_url=https://h/", "a", ""),
            ("token_url=https://h/?", "token=", ""),
            ('  File "a", line 1, in f\n    token_url = \'', "https://", ""),
        ],
    )
    def test_one_long_run_after_a_trigger(self, prefix: str, fill: str, suffix: str) -> None:
        self._assert_linear(lambda n: prefix + self._repeat(fill, n) + suffix)


def _fail_with(message: str) -> None:
    raise RuntimeError(message)


async def _fail_async(message: str) -> None:
    raise RuntimeError(message)


# Python's own layout: a frame line, then its source line indented two more.
_TB_HEAD = 'Traceback (most recent call last):\n  File "/app/python/app/x.py", line 7, in refresh\n'


class TestTracebackSourceLines:
    """A traceback's source lines are code read from a source file, so a
    credential-named assignment there is masked only when it is a literal."""

    @pytest.mark.parametrize(
        "source",
        [
            "token_data = await self._make_token_request(data)",
            "new_token = await oauth_provider.refresh_access_token(refresh_token)",
            "password = await vault.get(name)",
            "secret = get_secret(name)",
            'secret = self.config["x"]',
            "access_token = token.access_token",
            'refresh_token = creds.get("refresh_token")',
            "token = None",
            "token_data = yield from x",
            "if token == expected:",
            "token_data: dict = {}",
            "password=password,",
            'return {"access_token": access_token, "expires_in": 3600}',
            "def refresh(self, token: str) -> None:",
            'secret_stack = "".join(parts)',
        ],
    )
    def test_source_line_is_kept(self, source: str) -> None:
        text = f"{_TB_HEAD}    {source}\n    ^^^^^\nRuntimeError: password=SENTINEL-msg"

        out = redact_secrets_in_text(text)

        assert f"\n    {source}\n" in out
        assert "SENTINEL-msg" not in out

    def test_exception_group_source_line_is_kept(self) -> None:
        text = (
            "  + Exception Group Traceback (most recent call last):\n"
            '  |   File "/app/python/app/x.py", line 8, in group\n'
            "  |     async with asyncio.TaskGroup() as tg:\n"
            "  | ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)\n"
            "  +-+---------------- 1 ----------------\n"
            "    | Traceback (most recent call last):\n"
            '    |   File "/app/python/app/x.py", line 5, in refresh\n'
            "    |     token_data = await self._make_token_request(data)\n"
            "    |                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
            "    | RuntimeError: token=SENTINEL-group\n"
            "    +------------------------------------"
        )

        out = redact_secrets_in_text(text)

        assert "    |     token_data = await self._make_token_request(data)\n" in out
        assert "SENTINEL-group" not in out

    @pytest.mark.parametrize(
        "text",
        [
            # The exception line, also after a frame whose source is unavailable.
            f"{_TB_HEAD}    x = 1\nRuntimeError: password=SENTINEL-a",
            '  File "<generated>", line 2, in f\nRuntimeError: password=SENTINEL-a',
            # A message line under an exception line, or not indented as a source line.
            f"{_TB_HEAD}    x = 1\nValueError: bad\n    token=SENTINEL-a",
            '  File "/a.py", line 2, in f\n  token=SENTINEL-a',
            '  File "/a.py", line 2, in f\n\ttoken=SENTINEL-a',
            # Inside an exception group: same gutter, not two further spaces.
            '    |   File "/a.py", line 5, in f\n    | RuntimeError: token=SENTINEL-a',
            # The frame line itself, and a chained exception's message.
            '  File "/a.py", line 2, in f password=SENTINEL-a\n    x = 1',
            f"{_TB_HEAD}    x = 1\nKeyError: 'a'\n\nDuring handling of the above exception, another exception "
            "occurred:\n\nRuntimeError: secret: SENTINEL-a",
            # A SyntaxError's frame has no `, in`: the line under it is the parsed input.
            '  File "<unknown>", line 1\n    password=SENTINEL-a extra\n    ^\nSyntaxError: invalid syntax',
            # Literals on a source line: hard-coded, a captured local, or under a fake frame.
            f'{_TB_HEAD}    client = dict(password="SENTINEL-a")\nRuntimeError: x',
            f"{_TB_HEAD}    secret = b'SENTINEL-a'\nRuntimeError: x",
            "  File \"<generated>\", line 2, in g\n    password = 'SENTINEL-a'\nRuntimeError: x",
            '  File "x", line 1, in f\n    password="SENTINEL-a"',
            '  File "x", line 1, in f\r\n    password="SENTINEL-a"',
            # A pair inside a string or a comment on a source line is text, not code.
            f'{_TB_HEAD}    conn = connect("dbname=app user=app password=SENTINEL-a")\nRuntimeError: x',
            f'{_TB_HEAD}    q = "client_secret=SENTINEL-a&x=%s" % x\nRuntimeError: x',
            f"{_TB_HEAD}    hdr = 'X-Api-Key: SENTINEL-a'\nRuntimeError: x",
            f"{_TB_HEAD}    s = 'token=SENTINEL-a'\nRuntimeError: x",
            f'{_TB_HEAD}    url = f"a?x=1&token=SENTINEL-a"\nRuntimeError: x',
            f"{_TB_HEAD}    t = get()  # token=SENTINEL-a old\nRuntimeError: x",
            # Code-shaped text outside a traceback is indistinguishable from a pair.
            "    token = SENTINEL-a",
        ],
    )
    def test_everything_else_in_a_traceback_is_still_redacted(self, text: str) -> None:
        assert "SENTINEL-a" not in redact_secrets_in_text(text)

    def test_key_shapes_on_a_source_line_are_still_redacted(self) -> None:
        text = f"{_TB_HEAD}    client = OpenAI(api_key=key or sk-proj-SENTINELabcdefghijklmnop)\nRuntimeError: x"

        assert "SENTINELabcdefghijklmnop" not in redact_secrets_in_text(text)

    def test_formatted_traceback_keeps_the_failing_line(self) -> None:
        message = "upstream said password=" + "SENTINEL-real"
        try:
            token_data = _fail_with(message)  # noqa: F841
        except RuntimeError:
            record = _record("refresh failed", exc_info=sys.exc_info())

        out = RedactingFormatter("%(message)s").format(record)

        assert "    token_data = _fail_with(message)  # noqa: F841\n" in out
        assert "SENTINEL-real" not in out

    def test_formatted_traceback_keeps_await(self) -> None:
        message = "upstream said token=" + "SENTINEL-await"

        async def refresh() -> None:
            token_data = await _fail_async(message)  # noqa: F841

        coro = refresh()
        try:
            coro.send(None)
        except RuntimeError:
            record = _record("refresh failed", exc_info=sys.exc_info())

        out = RedactingFormatter("%(message)s").format(record)

        assert "    token_data = await _fail_async(message)  # noqa: F841\n" in out
        assert "SENTINEL-await" not in out

    def test_stack_info_keeps_the_calling_line(self) -> None:
        record = _record("checkpoint")
        # The credential-named variable is what puts its own line through the pair pass.
        secret_stack = "".join(traceback.format_stack())
        record.stack_info = f"Stack (most recent call last):\n{secret_stack}"

        out = RedactingFormatter("%(message)s").format(record)

        assert '    secret_stack = "".join(traceback.format_stack())\n' in out

    def test_a_hard_coded_pair_in_a_raise_message_is_still_redacted(self) -> None:
        try:
            raise RuntimeError("token=SENTINEL-raise")
        except RuntimeError:
            record = _record("refresh failed", exc_info=sys.exc_info())

        out = RedactingFormatter("%(message)s").format(record)

        assert '    raise RuntimeError("token=[REDACTED]")\n' in out
        assert "SENTINEL-raise" not in out

    @pytest.mark.parametrize("chained", [False, True])
    def test_syntax_error_input_is_still_redacted(self, chained: bool) -> None:
        stored = "{'password': 'SENTINEL-syntax', broken"
        try:
            try:
                ast.literal_eval(stored)
            except SyntaxError as err:
                if not chained:
                    raise
                raise ValueError("stored config is not a literal") from err
        except (SyntaxError, ValueError):
            record = _record("config unreadable", exc_info=sys.exc_info())

        out = RedactingFormatter("%(message)s").format(record)

        assert "SyntaxError" in out
        assert "SENTINEL-syntax" not in out

    def test_captured_locals_of_a_frame_without_source_are_still_redacted(self) -> None:
        code = compile("def g():\n    password = 'SENTINEL-local'\n    1 / 0\n", "<generated>", "exec")
        scope: dict = {}
        exec(code, scope)
        try:
            scope["g"]()
        except ZeroDivisionError as exc:
            text = "".join(traceback.TracebackException.from_exception(exc, capture_locals=True).format())

        assert "    password = 'SENTINEL-local'" in text
        assert "SENTINEL-local" not in redact_secrets_in_text(text)


_SECRET = "SENTINEL-f12"
_OAUTH_CALLBACK = (
    "OAuth token exchange completed for instance c-1 (connector_type: GMAIL). "
    "Token details - has_access_token: True, has_refresh_token: True, token_type: Bearer, expires_in: 3599, "
    "refresh_token_expires_in: 7776000, scope: openid email, has_id_token: False, uid: None, "
    "created_at: 2026-11-08 10:00:00"
)


class TestNonSecretValues:
    """Under a credential name, a value that says whether a credential exists,
    when it expires, or where an OAuth endpoint is, is not a secret."""

    @pytest.mark.parametrize(
        "text",
        [
            _OAUTH_CALLBACK,
            "Missing OAuth client credentials (client_id: True, client_secret: False).",
            "delegated Graph client (tenant=common, access_token=present, refresh_token=missing, expires_at=1730000000)",
            "refresh_token=None",
            "access_token=MISSING",
            "has_refresh_token=True",
            "password_changed=False",
            "token: undefined",
            '"refresh_token": null',
            '{"has_refresh_token": true}',
            "token_expires_at=1730000000",
            "refresh_token_expires_at=1730000000000",
            "token_expires_at=1730000000.123",
            "token_expires_at=2026-11-08T10:00:00+00:00",
            "token_expires_at=2026-11-08 10:00:00",
            "client_secret_expires_at=1762600000",
            "refreshed_token_expires_at=1730000000",
            "tokenIssuedAt: 1730000000",
            "refresh_token_expires_in: 7776000",
            "existing_token_expire_in_seconds=7776000",
            "{'token_expires_at': '1730000000'}",
            "token_url=https://oauth2.googleapis.com/token",
            "token_uri: https://oauth2.googleapis.com/token",
            "token_endpoint=https://h.example.com:8443/oauth/token",
            "tokenEndpoint: https://h.example.com/oauth/token",
            "authorization_url=https://accounts.google.com/o/oauth2/v2/auth",
            "authorization_endpoint=https://h.example.com/authorize",
            "TOKEN_URL=HTTPS://OAUTH2.GOOGLEAPIS.COM/TOKEN",
            "{'tokenUrl': 'https://login.microsoftonline.com/common/oauth2/v2.0/token', 'clientId': 'abc'}",
        ],
    )
    def test_value_that_describes_a_credential_is_kept(self, text: str) -> None:
        assert redact_secrets_in_text(text) == text

    def test_a_label_still_swallows_the_pair_after_it(self) -> None:
        text = "Current credentials: access_token=MISSING, refresh_token=None"

        assert redact_secrets_in_text(text) == f"Current credentials: {REDACTED}, refresh_token=None"

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            (f"refresh_token=1//0g{_SECRET}", _SECRET),
            (f"{{'refresh_token': '1//0g{_SECRET}'}}", _SECRET),
            (f'"refresh_token": "{_SECRET}"', _SECRET),
            (f"has_token: Bearer {_SECRET}", _SECRET),
            (f"client_secret: GOCSPX-{_SECRET}", _SECRET),
            ("password=changeme", "changeme"),
            # Presence words count only bare and whole.
            ("refresh_token='None'", "None"),
            ('"refresh_token": "null"', "null"),
            ("refresh_token=b'None'", "None"),
            (f"refresh_token=None_{_SECRET}", _SECRET),
            (f"refresh_token=None.{_SECRET}", _SECRET),
            (f"refresh_token=None-{_SECRET}", _SECRET),
            (f"refresh_token=None:{_SECRET}", _SECRET),
            (f"refresh_token=None/{_SECRET}", _SECRET),
            (f"has_refresh_token=True{_SECRET}", _SECRET),
            # A long number is a time only under a name that says so, after its last time word.
            ("password=73915528", "73915528"),
            ("token=1730000000", "1730000000"),
            ("access_token=1730000000.5", "1730000000.5"),
            ("token_in=12345678901", "12345678901"),
            ("updated_password=73915528", "73915528"),
            ("issued_token=84736251", "84736251"),
            ("refreshed_token=1730000000", "1730000000"),
            ("created_secret=1234-5678-9012", "1234-5678-9012"),
            ("exp_token=73915528", "73915528"),
            (f"token_expires_at={_SECRET}", _SECRET),
            (f"token_expires_at=1730000000{_SECRET}", _SECRET),
            # A URL is kept only under an OAuth endpoint name, as http(s) scheme, host and path.
            (f"token_url=https://h.example.com/t?refresh_token={_SECRET}", _SECRET),
            (f"token_url=https://h.example.com/t?auth={_SECRET}", _SECRET),
            (f"token_url=https://h.example.com/t?jwt={_SECRET}", _SECRET),
            (f"token_url=https://h.example.com/t#access_token={_SECRET}", _SECRET),
            (f"token_url=https://app:{_SECRET}@h.example.com/t", _SECRET),
            (f"token_url=https://h.example.com/t%3Ftoken%3D{_SECRET}", _SECRET),
            (f"token_url='https://h.example.com/t?x={_SECRET}'", _SECRET),
            (f"token_url=/x/{_SECRET}", _SECRET),
            (f"token_url=redis://{_SECRET}", _SECRET),
            (f"token_url=x://{_SECRET}", _SECRET),
            (f"token_url={_SECRET}://h", _SECRET),
            (f"token_url=javascript://{_SECRET}/x", _SECRET),
            (f"secret_url=https://hooks.slack.com/services/T/B/{_SECRET}", _SECRET),
            (f"password_reset_url=https://app.example.com/reset/{_SECRET}", _SECRET),
            (f"api_key_url=https://h.example.com/k/{_SECRET}", _SECRET),
            (f"webhook_secret_url=https://h.example.com/{_SECRET}", _SECRET),
            (f"reset_token_url=https://app.example.com/reset/{_SECRET}", _SECRET),
            (f"webhook_token_url=https://hooks.slack.com/services/T/B/{_SECRET}", _SECRET),
            (f"slack_token_endpoint=https://h.example.com/{_SECRET}", _SECRET),
            (f"secret_token_url=https://h.example.com/{_SECRET}", _SECRET),
            (f"token_url_secret=https://h.example.com/{_SECRET}", _SECRET),
            (f"oauth.tokenUrl=https://h.example.com/{_SECRET}", _SECRET),
            (f"connection_string=postgresql://app:{_SECRET}@db/x", _SECRET),
            # The later passes, and the pair rule itself, still apply inside a kept URL.
            ("token_url=https://h.example.com/sk-proj-SENTINELabcdefghijklmnop/t", "sk-proj-SENTINELabcdefghijklmnop"),
            (f"token_url=https://h.example.com/{JWT}", JWT),
            (f"token_url=https://h.example.com/password:{_SECRET}", _SECRET),
            (f"token_url=https://h.example.com/t,client_secret={_SECRET}", _SECRET),
            (f"token_url=https://h.example.com/t;client_secret={_SECRET}", _SECRET),
            (f"token_url=https://h.example.com/t,client_secret={_SECRET}&x=1", _SECRET),
            # The characters of _PUBLIC_URL's class that a secret can hide behind, one per row: dropping one must fail its row.
            (f"token_url=https://{_SECRET}@h/t", _SECRET),
            (f"token_url=https://h/t#{_SECRET}", _SECRET),
            (f"token_url=https://h/t%3Fauth%3D{_SECRET}", _SECRET),
            (f'{{"token_url": "https://h/t?auth={_SECRET}"}}', _SECRET),
            (f"token_url=https://h/t/auth={_SECRET}", _SECRET),
            (f"token_url=https://h/t?{_SECRET}", _SECRET),
            (f"token_url=https://h/t\\x3fauth\\x3d{_SECRET}", _SECRET),      # an escaped query
            # The endpoint names are exact: a refresh or access token's own URL can hold the token.
            (f"refresh_token_url=https://h/{_SECRET}", _SECRET),
            (f"access_token_url=https://h/{_SECRET}", _SECRET),
        ],
    )
    def test_credential_is_still_masked(self, text: str, secret: str) -> None:
        out = redact_secrets_in_text(text)

        assert secret not in out
        assert "REDACTED" in out

    @pytest.mark.parametrize(
        "source", ['token_url = "https://oauth2.googleapis.com/token"', 'refresh_token_expires_in = "7776000"'],
    )
    def test_source_line_literal_is_kept(self, source: str) -> None:
        text = f"{_TB_HEAD}    {source}\nRuntimeError: refresh failed"

        assert redact_secrets_in_text(text) == text

    @pytest.mark.parametrize(("source", "secret"), [('updated_password = "73915528"', "73915528"), ('password = "None"', "None")])
    def test_source_line_literal_is_still_masked(self, source: str, secret: str) -> None:
        out = redact_secrets_in_text(f"{_TB_HEAD}    {source}\nRuntimeError: refresh failed")

        assert secret not in out
        assert REDACTED in out

    def test_text_and_json_lines_keep_the_description_and_mask_the_token(self) -> None:
        record = _record(f"{_OAUTH_CALLBACK}, refresh_token={_SECRET}")

        text = TextFormatter("%(message)s").format(record)
        message = json.loads(JsonFormatter().format(record))["message"]

        for out in (text, message):
            assert out == f"{_OAUTH_CALLBACK}, refresh_token={REDACTED}"

    def test_text_fields_keep_booleans_and_times(self) -> None:
        record = _record("oauth.callback")
        record.has_refresh_token = True
        record.token_expires_at = 1730000000

        out = TextFormatter("%(message)s").format(record)

        assert out == "oauth.callback | has_refresh_token=true token_expires_at=1730000000"


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


class TestInstallation:
    def test_create_logger_handlers_redact(self) -> None:
        logger = create_logger("test_log_redaction_service")
        try:
            assert logger.handlers
            # By attribute: tests that reload app.utils.logger leave new formatter classes behind.
            assert all(getattr(h.formatter, "_pipeshub_redacting", False) for h in logger.handlers)
        finally:
            for handler in logger.handlers:
                handler.close()
            logger.handlers.clear()

    def test_root_console_handler_redacts(self) -> None:
        # A fresh interpreter: under pytest the root logger already has
        # handlers, so the module adds no console handler of its own.
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
        assert "password=" in result.stdout
        assert "SENTINEL-basic" not in result.stdout + result.stderr

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


class TestRedactErrorForLog:
    """Provider error text on its way to a log line (app.utils.log_redaction.redact_error_for_log)."""

    @pytest.mark.parametrize("ours", [
        'File "/app/python/app/core/org_context.py", line 3',
        "KeyError: org_id_hash",
        "org_context_missing site=x",
        "account_service.get_user failed",
        "org-abcdef failed",
        "org_context1.py",
        "org-a1b2(x)",
        "proj_" + "a" * 20,
        "org-abcd\u0663",
    ])
    def test_our_own_names_survive(self, ours: str) -> None:
        from app.utils.log_redaction import redact_error_for_log

        assert redact_error_for_log(ours) == ours

    @pytest.mark.parametrize("provider_id", ["org-AbCd1234", "proj_abc123XYZ", "acct-9f8e7d"])
    def test_provider_account_ids_are_masked(self, provider_id: str) -> None:
        from app.utils.log_redaction import redact_error_for_log

        assert redact_error_for_log(f"quota for {provider_id} exceeded") == "quota for [redacted] exceeded"

    @pytest.mark.parametrize(("text", "expected"), [
        # The digit counts even past where `(?!\.py|\()` ends the match.
        ("org-abcd-1(", "[redacted]1("),
        ("account-x-org-1234", "[redacted]"),
        ("org-aaaa-org_x1 org-bbbb", "[redacted] org-bbbb"),
    ])
    def test_digit_anywhere_in_the_id_masks_it(self, text: str, expected: str) -> None:
        from app.utils.log_redaction import redact_error_for_log

        assert redact_error_for_log(text) == expected

    def test_urls_and_keys_masked_and_capped(self) -> None:
        from app.utils.log_redaction import redact_error_for_log

        out = redact_error_for_log("GET https://gen.example/v1?key=AIzaSyA1234567890abcdefghijklmnopqrstuv failed" + "x" * 2000)
        assert out.startswith("GET [url] failed") and "AIza" not in out and len(out) == 1000
