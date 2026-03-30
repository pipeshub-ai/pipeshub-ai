from __future__ import annotations

import logging
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest
from dotenv import load_dotenv
import pytest_mask_secrets.plugin as _pytest_mask_secrets_plugin
from pytest_mask_secrets.plugin import mask_secrets_key

from secret_mask_constants import (
    ENV_KEY_NAME_SUBSTRINGS_FOR_MASKING,
    REGEX_REDACTION_PATTERNS_AFTER_LITERALS,
    SECRET_ENV_KEYS_FOR_MASKING,
    SECRET_ENV_VALUE_MIN_LEN,
)

_THIS_DIR = Path(__file__).resolve().parent
_HELPER_DIR = _THIS_DIR / "helper"
_REPORTS_DIR = _THIS_DIR / "reports"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

_REDACTING_LOG_FACTORY_INSTALLED = False


def _collect_secret_values() -> List[str]:
    """Env literals registered for masking (explicit list + dynamic sensitive key names)."""
    values: list[str] = []
    for key in SECRET_ENV_KEYS_FOR_MASKING:
        v = os.environ.get(key, "").strip()
        if v:
            values.append(v)
    for k, v in os.environ.items():
        if len(v.strip()) < SECRET_ENV_VALUE_MIN_LEN:
            continue
        ku = k.upper()
        if any(s in ku for s in ENV_KEY_NAME_SUBSTRINGS_FOR_MASKING):
            values.append(v.strip())
    return sorted(set(values), key=len, reverse=True)


def _secrets_set_for_masking() -> set[str]:
    """
    Same secret strings pytest-mask-secrets uses on reports (plugin.py pytest_runtest_logreport),
    plus Pipeshub env literals (also added to the stash in pytest_configure).
    """
    secrets: set[str] = set()
    if os.environ.get("MASK_SECRETS_AUTO", "") not in ("0", ""):
        candidates = re.compile("(TOKEN|PASSWORD|PASSWD|SECRET)")
        mine = re.compile(r"MASK_SECRETS(_AUTO)?\b")
        secrets |= {
            os.environ[k]
            for k in os.environ
            if candidates.search(k) and not mine.match(k)
        }
    if "MASK_SECRETS" in os.environ:
        vars_ = os.environ["MASK_SECRETS"].split(",")
        secrets |= {os.environ[k] for k in vars_ if k in os.environ}
    stash = getattr(_pytest_mask_secrets_plugin, "_stash", None)
    if stash is not None:
        secrets |= stash.get(mask_secrets_key, set())
    secrets |= set(_collect_secret_values())
    return {s for s in secrets if s}


def _mask_plaintext_secrets(text: str) -> str:
    """
    Identical substitution to pytest-mask-secrets: one regex built from re.escape(secret),
    replacement '*****' (see plugin.py).
    """
    if not text:
        return text
    secrets = _secrets_set_for_masking()
    if not secrets:
        return text
    escaped = [re.escape(s) for s in sorted(secrets, key=len, reverse=True) if s]
    if not escaped:
        return text
    pattern = re.compile(f"({'|'.join(escaped)})")
    return pattern.sub("*****", text)


def _redact_text(text: str) -> str:
    if not text:
        return text

    redacted = _mask_plaintext_secrets(text)
    for pattern, repl in REGEX_REDACTION_PATTERNS_AFTER_LITERALS:
        redacted = re.sub(pattern, repl, redacted)
    return redacted


def _install_redacting_log_record_factory() -> None:
    """
    Redact secrets in all log output (including pytest live log).

    Installed at import time so logging before pytest_configure is safe. Child loggers
    do not inherit root filters; wrapping getMessage on every LogRecord is reliable.
    After redaction, msg/args are replaced so handlers that read fields directly do not
    retain raw secrets.
    """
    global _REDACTING_LOG_FACTORY_INSTALLED
    if _REDACTING_LOG_FACTORY_INSTALLED:
        return

    prev_factory = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = prev_factory(*args, **kwargs)  # type: ignore[misc]
        orig_getMessage = record.getMessage

        def redacting_getMessage() -> str:
            text = _redact_text(orig_getMessage())
            record.msg = text
            record.args = ()
            return text

        record.getMessage = redacting_getMessage  # type: ignore[method-assign]
        return record

    logging.setLogRecordFactory(factory)
    _REDACTING_LOG_FACTORY_INSTALLED = True


def _load_env() -> None:
    """
    Load .env first (typically only PIPESHUB_TEST_ENV=local or prod).
    Then load the matching env file so credentials stay out of .env:
    - PIPESHUB_TEST_ENV=local  -> .env.local
    - PIPESHUB_TEST_ENV=prod   -> .env.prod
    """
    env_path = _THIS_DIR / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)

    test_env = os.getenv("PIPESHUB_TEST_ENV", "").strip().lower()
    if test_env == "local":
        local_env = _THIS_DIR / ".env.local"
        if local_env.exists():
            load_dotenv(dotenv_path=local_env, override=True)
        os.environ.pop("PIPESHUB_USER_BEARER_TOKEN", None)
    elif test_env == "prod":
        prod_env = _THIS_DIR / ".env.prod"
        if prod_env.exists():
            load_dotenv(dotenv_path=prod_env, override=True)


def _init_global_test_env() -> None:
    """Load integration-tests/.env then .env.local or .env.prod. Neo4j uses TEST_NEO4J_* only."""
    _load_env()


_init_global_test_env()
_install_redacting_log_record_factory()

from integration_report import TestReportEntry, write_html_report  # noqa: E402
from local_auth import obtain_local_oauth_credentials  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

# Module-level ref so pytest_runtest_logreport can append even when report.config is missing (e.g. some pytest versions)
_integration_test_reports: List[TestReportEntry] = []


@pytest.fixture(scope="session", autouse=True)
def local_oauth_credentials(pytestconfig: pytest.Config) -> None:
    """
    When running in local mode without CLIENT_ID/CLIENT_SECRET, obtain them from the backend
    (initAuth -> authenticate -> create OAuth app) and set in env so PipeshubClient works.
    """
    if os.getenv("PIPESHUB_TEST_ENV") != "local":
        return
    if os.getenv("CLIENT_ID") and os.getenv("CLIENT_SECRET"):
        return
    base_url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not base_url:
        return
    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret

    secrets_set = pytestconfig.stash[mask_secrets_key]
    secrets_set.add(client_id)
    secrets_set.add(client_secret)


def get_pipeshub_client() -> PipeshubClient:
    """Convenience helper for tests that prefer direct construction."""
    return PipeshubClient()


def pytest_sessionstart(session) -> None:  # type: ignore[override]
    """
    Pytest hook to validate that critical env vars are present.

    Prod (PIPESHUB_TEST_ENV=prod): require PIPESHUB_BASE_URL, CLIENT_ID, CLIENT_SECRET, TEST_NEO4J_*.
    Local (PIPESHUB_TEST_ENV=local): require PIPESHUB_BASE_URL, TEST_NEO4J_*,
    and either (CLIENT_ID + CLIENT_SECRET) or (PIPESHUB_TEST_USER_EMAIL + PIPESHUB_TEST_USER_PASSWORD).
    """
    test_env = os.getenv("PIPESHUB_TEST_ENV", "").strip().lower()
    env_file = ".env.prod" if test_env == "prod" else (".env.local" if test_env == "local" else "none")
    base_url = os.getenv("PIPESHUB_BASE_URL", "")
    log = logging.getLogger("integration-tests")
    log.info(
        "PIPESHUB_TEST_ENV=%s, env file=%s, base_url=%s",
        test_env or "(not set)",
        env_file,
        base_url or "(not set)",
    )

    missing = []
    is_local = test_env == "local"

    if not os.getenv("PIPESHUB_BASE_URL"):
        missing.append("PIPESHUB_BASE_URL")

    if is_local:
        for key in ["TEST_NEO4J_URI", "TEST_NEO4J_USERNAME", "TEST_NEO4J_PASSWORD"]:
            if not os.getenv(key):
                missing.append(key)
        has_creds = os.getenv("CLIENT_ID") and os.getenv("CLIENT_SECRET")
        has_test_user = os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv(
            "PIPESHUB_TEST_USER_PASSWORD"
        )
        if not has_creds and not has_test_user:
            missing.append(
                "CLIENT_ID+CLIENT_SECRET or PIPESHUB_TEST_USER_EMAIL+PIPESHUB_TEST_USER_PASSWORD"
            )
    else:
        # Prod: use OAuth2 client_credentials (CLIENT_ID + CLIENT_SECRET)
        if not os.getenv("CLIENT_ID"):
            missing.append("CLIENT_ID")
        if not os.getenv("CLIENT_SECRET"):
            missing.append("CLIENT_SECRET")
        for key in ["TEST_NEO4J_URI", "TEST_NEO4J_USERNAME", "TEST_NEO4J_PASSWORD"]:
            if not os.getenv(key):
                missing.append(key)

    if missing:
        warnings.warn(
            f"Missing integration env vars: {', '.join(sorted(set(missing)))}",
            UserWarning,
            stacklevel=2,
        )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Initialize report collection, register secrets for pytest-mask-secrets, and log redaction."""
    global _integration_test_reports
    _integration_test_reports = []
    config._integration_test_reports = _integration_test_reports  # type: ignore[attr-defined]
    config._integration_session_start = time.monotonic()  # type: ignore[attr-defined]

    # trylast=True: plugin has initialized mask_secrets_key stash.
    secrets_set = config.stash[mask_secrets_key]
    for value in _collect_secret_values():
        secrets_set.add(value)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Collect pass/fail/skip + failure text for HTML report (pytest-mask-secrets handles pytest output)."""
    if report.when != "call":
        return
    config = getattr(report, "config", None)
    reports: List[TestReportEntry] = (
        getattr(config, "_integration_test_reports", None) if config else None
    )
    if reports is None:
        reports = _integration_test_reports
    longrepr = getattr(report, "longrepr", None)
    longreprtext = getattr(report, "longreprtext", None)
    if longreprtext:
        full_text = _redact_text(longreprtext.strip())
    elif longrepr is not None:
        full_text = _redact_text(str(longrepr).strip())
    else:
        full_text = ""

    duration = float(getattr(report, "duration", 0) or 0)
    outcome = report.outcome
    err_full = full_text if outcome == "failed" and full_text else None

    stdout_captured = None
    stderr_captured = None
    for name, content in getattr(report, "sections", []) or []:
        if name == "Captured stdout call" or name == "Captured stdout":
            stdout_captured = (stdout_captured or "") + _redact_text(content)
        elif name == "Captured stderr call" or name == "Captured stderr":
            stderr_captured = (stderr_captured or "") + _redact_text(content)

    reports.append(
        TestReportEntry(
            nodeid=report.nodeid,
            outcome=outcome,
            duration=duration,
            err_full=err_full,
            stdout_captured=stdout_captured,
            stderr_captured=stderr_captured,
        )
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write integration test HTML report under reports/ with timestamp."""
    reports: List[TestReportEntry] = getattr(
        session.config, "_integration_test_reports", None,
    )
    if reports is None:
        reports = _integration_test_reports
    env_label = "local" if os.getenv("PIPESHUB_TEST_ENV") == "local" else "remote"
    base_url = os.getenv("PIPESHUB_BASE_URL", "")
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    timestamp_file = now.strftime("%Y-%m-%d_%H-%M-%S")
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    session_wall_s = None
    start = getattr(session.config, "_integration_session_start", None)
    if start is not None:
        session_wall_s = time.monotonic() - start

    report_path_html = _REPORTS_DIR / f"INTEGRATION_TEST_REPORT_{timestamp_file}.html"
    write_html_report(
        reports,
        report_path_html,
        timestamp_title=timestamp,
        timestamp_file=timestamp_file,
        env_label=env_label,
        base_url=base_url or "(not set)",
        exitstatus=exitstatus,
        session_wall_s=session_wall_s,
    )

