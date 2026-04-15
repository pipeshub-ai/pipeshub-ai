from __future__ import annotations

import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List

import pytest
from neo4j import Driver, GraphDatabase
from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent
_HELPER_DIR = _THIS_DIR / "helper"
_SAMPLE_DATA_DIR = _THIS_DIR / "sample-data"
_REPORTS_DIR = _THIS_DIR / "reports"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
if str(_SAMPLE_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_SAMPLE_DATA_DIR))


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

    refresh_tokens_env = _THIS_DIR / ".env.refresh_tokens"
    if refresh_tokens_env.exists():
        load_dotenv(dotenv_path=refresh_tokens_env, override=True)


def _init_global_test_env() -> None:
    """Load integration-tests/.env then .env.local or .env.prod. Neo4j uses TEST_NEO4J_* only."""
    _load_env()


_init_global_test_env()

from integration_report import TestReportEntry, write_html_report  # noqa: E402
from local_auth import obtain_local_oauth_credentials  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402
from sample_data import ensure_sample_data_files_root  # noqa: E402

# Module-level ref so pytest_runtest_logreport can append even when report.config is missing (e.g. some pytest versions)
_integration_test_reports: List[TestReportEntry] = []


@pytest.fixture(scope="session", autouse=True)
def local_oauth_credentials() -> None:
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


def get_pipeshub_client() -> PipeshubClient:
    """Convenience helper for tests that prefer direct construction."""
    return PipeshubClient()


@pytest.fixture(scope="session")
def pipeshub_client() -> PipeshubClient:
    """Session-scoped Pipeshub client (global for all integration tests)."""
    return PipeshubClient()


@pytest.fixture(scope="session")
def neo4j_driver() -> Generator[Driver, None, None]:
    """Session-scoped Neo4j driver."""
    uri = os.getenv("TEST_NEO4J_URI")
    user = os.getenv("TEST_NEO4J_USERNAME")
    password = os.getenv("TEST_NEO4J_PASSWORD")

    if not uri or not user or not password:
        pytest.skip(
            "TEST_NEO4J_URI / TEST_NEO4J_USERNAME / TEST_NEO4J_PASSWORD not set; skipping connector integration tests."
        )

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        yield driver
    finally:
        driver.close()


@pytest.fixture(scope="session")
def sample_data_root() -> Path:
    """Session-scoped path to sample data files from GitHub."""
    return ensure_sample_data_files_root()


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
    """Initialize report collection for the HTML integration report."""
    global _integration_test_reports
    _integration_test_reports = []
    config._integration_test_reports = _integration_test_reports  # type: ignore[attr-defined]
    config._integration_session_start = time.monotonic()  # type: ignore[attr-defined]


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Collect pass/fail/skip + failure text for HTML report."""
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
        full_text = longreprtext.strip()
    elif longrepr is not None:
        full_text = str(longrepr).strip()
    else:
        full_text = ""

    duration = float(getattr(report, "duration", 0) or 0)
    outcome = report.outcome
    err_full = full_text if outcome == "failed" and full_text else None

    stdout_captured = None
    stderr_captured = None
    for name, content in getattr(report, "sections", []) or []:
        if name == "Captured stdout call" or name == "Captured stdout":
            stdout_captured = (stdout_captured or "") + content
        elif name == "Captured stderr call" or name == "Captured stderr":
            stderr_captured = (stderr_captured or "") + content

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

