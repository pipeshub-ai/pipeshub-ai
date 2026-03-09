import logging
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Tuple

import pytest
from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent
_HELPER_DIR = _THIS_DIR / "helper"
_REPORTS_DIR = _THIS_DIR / "reports"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

from local_auth import obtain_local_oauth_credentials  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402


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


def pytest_sessionstart(session) -> None:  # type: ignore[override]
    """
    Pytest hook to validate that critical env vars are present.

    For local mode (PIPESHUB_TEST_ENV=local): require PIPESHUB_BASE_URL, TEST_NEO4J_*,
    and either (CLIENT_ID + CLIENT_SECRET) or (PIPESHUB_TEST_USER_EMAIL + PIPESHUB_TEST_USER_PASSWORD).
    For non-local: require PIPESHUB_BASE_URL, PIPESHUB_USER_BEARER_TOKEN, TEST_NEO4J_*.
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
        has_bearer = bool((os.getenv("PIPESHUB_USER_BEARER_TOKEN") or "").strip())
        if not has_creds and not has_test_user and not has_bearer:
            missing.append(
                "CLIENT_ID+CLIENT_SECRET or PIPESHUB_TEST_USER_* or PIPESHUB_USER_BEARER_TOKEN"
            )
    else:
        if not os.getenv("PIPESHUB_USER_BEARER_TOKEN"):
            missing.append("PIPESHUB_USER_BEARER_TOKEN")
        for key in ["TEST_NEO4J_URI", "TEST_NEO4J_USERNAME", "TEST_NEO4J_PASSWORD"]:
            if not os.getenv(key):
                missing.append(key)

    if missing:
        warnings.warn(
            f"Missing integration env vars: {', '.join(sorted(set(missing)))}",
            UserWarning,
            stacklevel=2,
        )


def pytest_configure(config: pytest.Config) -> None:
    """Initialize list to collect test outcomes for the report."""
    config._integration_test_reports = []  # type: ignore[attr-defined]


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Collect pass/fail/skip for each test so we can write the report."""
    if report.when != "call":
        return
    config = getattr(report, "config", None)
    reports: List[Tuple[str, str, Any]] = getattr(
        config, "_integration_test_reports", None
    )
    if reports is None:
        return
    longrepr = getattr(report, "longrepr", None)
    longreprtext = getattr(report, "longreprtext", None)
    if longreprtext:
        err_snippet = longreprtext.strip()[:800]
    elif longrepr is not None:
        err_snippet = str(longrepr).strip()[:800]
    else:
        err_snippet = None
    reports.append((report.nodeid, report.outcome, err_snippet))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write integration test report to reports/ with a timestamped filename."""
    reports: List[Tuple[str, str, Any]] = getattr(
        session.config, "_integration_test_reports", [],
    )
    passed = sum(1 for _, outcome, _ in reports if outcome == "passed")
    failed = sum(1 for _, outcome, _ in reports if outcome == "failed")
    skipped = sum(1 for _, outcome, _ in reports if outcome == "skipped")
    total = len(reports)
    env_label = "local" if os.getenv("PIPESHUB_TEST_ENV") == "local" else "remote"
    base_url = os.getenv("PIPESHUB_BASE_URL", "")
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    timestamp_file = now.strftime("%Y-%m-%d_%H-%M-%S")
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORTS_DIR / f"INTEGRATION_TEST_REPORT_{timestamp_file}.md"
    lines = [
        "# Integration Test Report",
        "",
        f"**Generated:** {timestamp}",
        f"**Environment:** {env_label}",
        f"**Base URL:** {base_url or '(not set)'}",
        "",
        "## Summary",
        "",
        "| Result | Count |",
        "|--------|-------|",
        f"| Passed | {passed} |",
        f"| Failed | {failed} |",
        f"| Skipped | {skipped} |",
        f"| **Total** | **{total}** |",
        "",
        f"**Exit status:** {exitstatus} (0 = success)",
        "",
    ]
    failed_items = [(n, s) for n, o, s in reports if o == "failed"]
    if failed_items:
        lines.append("## Failed tests")
        lines.append("")
        for nodeid, err_snippet in failed_items:
            lines.append(f"- `{nodeid}`")
            if err_snippet:
                lines.append("  ```")
                for line in (err_snippet or "").strip().split("\n")[:10]:
                    lines.append(f"  {line}")
                lines.append("  ```")
        lines.append("")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

