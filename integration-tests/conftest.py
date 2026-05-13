from __future__ import annotations

import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, TYPE_CHECKING, AsyncGenerator, List, Generator

import pytest
import pytest_asyncio
from dotenv import load_dotenv

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol

_THIS_DIR = Path(__file__).resolve().parent
_HELPER_DIR = _THIS_DIR / "helper"
_SAMPLE_DATA_DIR = _THIS_DIR / "sample-data"
_REPORTS_DIR = _THIS_DIR / "reports"
_BACKEND_PYTHON = _THIS_DIR.parent / "backend" / "python"

if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
if str(_SAMPLE_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_SAMPLE_DATA_DIR))
if str(_BACKEND_PYTHON) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PYTHON))

# Import after backend path is added to sys.path
from helper.config_service_fixture import config_service  # noqa: F401, E402


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
    """Load integration-tests/.env then .env.local or .env.prod. Map TEST_NEO4J_* and TEST_ARANGO_* to backend vars."""
    _load_env()
    _setup_neo4j_env_vars()
    _setup_arango_env_vars()


def _setup_neo4j_env_vars() -> None:
    """
    Map TEST_NEO4J_* env vars to NEO4J_* for backend provider compatibility.
    The backend Neo4jProvider reads from NEO4J_* env vars.
    """
    mappings = [
        ("TEST_NEO4J_URI", "NEO4J_URI"),
        ("TEST_NEO4J_USERNAME", "NEO4J_USERNAME"),
        ("TEST_NEO4J_PASSWORD", "NEO4J_PASSWORD"),
        ("TEST_NEO4J_DATABASE", "NEO4J_DATABASE"),
    ]
    for test_var, backend_var in mappings:
        value = os.getenv(test_var)
        if value:
            os.environ[backend_var] = value


def _setup_arango_env_vars() -> None:
    """
    Map TEST_ARANGO_* env vars to ARANGO_* for backend provider compatibility.

    ``ArangoHTTPProvider.connect()`` reads ``ARANGO_*`` from the process environment when
    ``config_service`` is None (integration tests); production uses ConfigurationService.
    """
    mappings = [
        ("TEST_ARANGO_URL", "ARANGO_URL"),
        ("TEST_ARANGO_USERNAME", "ARANGO_USERNAME"),
        ("TEST_ARANGO_PASSWORD", "ARANGO_PASSWORD"),
        ("TEST_ARANGO_DB_NAME", "ARANGO_DB_NAME"),
    ]
    for test_var, backend_var in mappings:
        value = os.getenv(test_var)
        if value:
            os.environ[backend_var] = value


_init_global_test_env()

from ai_models_setup import (  # noqa: E402
    SeededAIModel,
    setup_test_llm_model,
    teardown_test_llm_model,
)
from integration_report import TestReportEntry, write_html_report  # noqa: E402
from local_auth import obtain_local_oauth_credentials  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402
from sample_data import ensure_sample_data_files_root  # noqa: E402

# Module-level refs so pytest_runtest_logreport can merge even when report.config is missing
_integration_test_reports_by_nodeid: Dict[str, TestReportEntry] = {}
_integration_test_report_order: List[str] = []


def _longrepr_and_streams(report: pytest.TestReport) -> tuple[str, str | None, str | None, str | None]:
    """Failure text and captured streams from a single phase report."""
    longrepr = getattr(report, "longrepr", None)
    longreprtext = getattr(report, "longreprtext", None)
    if longreprtext:
        full_text = longreprtext.strip()
    elif longrepr is not None:
        full_text = str(longrepr).strip()
    else:
        full_text = ""

    outcome = report.outcome
    err_full = full_text if outcome == "failed" and full_text else None

    stdout_captured = None
    stderr_captured = None
    for name, content in getattr(report, "sections", []):
        if name.startswith("Captured stdout"):
            stdout_captured = (stdout_captured or "") + content
        elif name.startswith("Captured stderr"):
            stderr_captured = (stderr_captured or "") + content
    return full_text, err_full, stdout_captured, stderr_captured


def _merge_phase_report(
    existing: TestReportEntry,
    report: pytest.TestReport,
    *,
    full_text: str,
    err_full: str | None,
    stdout_captured: str | None,
    stderr_captured: str | None,
) -> TestReportEntry:
    """Combine setup/call/teardown into one row per test (matches JUnit overall outcome)."""
    when = report.when
    duration = float(getattr(report, "duration", 0) or 0)
    new_dur = existing.duration + duration

    def combine_err(prefix: str, prev: str | None, nxt: str | None) -> str | None:
        if not nxt:
            return prev
        block = f"--- Failure during {prefix} ---\n{nxt}"
        if prev:
            return f"{prev}\n\n{block}"
        return block

    if report.outcome == "failed":
        phase_err = err_full or (full_text.strip() if full_text.strip() else None)
        merged_err = combine_err(str(when), existing.err_full, phase_err)
        return TestReportEntry(
            nodeid=existing.nodeid,
            outcome="failed",
            duration=new_dur,
            err_full=merged_err,
            stdout_captured=existing.stdout_captured or stdout_captured,
            stderr_captured=existing.stderr_captured or stderr_captured,
        )

    if existing.outcome == "failed":
        return TestReportEntry(
            nodeid=existing.nodeid,
            outcome="failed",
            duration=new_dur,
            err_full=existing.err_full,
            stdout_captured=existing.stdout_captured or stdout_captured,
            stderr_captured=existing.stderr_captured or stderr_captured,
        )

    if when == "call":
        if report.outcome == "skipped":
            return TestReportEntry(
                nodeid=existing.nodeid,
                outcome="skipped",
                duration=new_dur,
                err_full=existing.err_full,
                stdout_captured=existing.stdout_captured or stdout_captured,
                stderr_captured=existing.stderr_captured or stderr_captured,
            )
        if report.outcome == "passed":
            return TestReportEntry(
                nodeid=existing.nodeid,
                outcome="passed",
                duration=new_dur,
                err_full=existing.err_full,
                stdout_captured=existing.stdout_captured or stdout_captured,
                stderr_captured=existing.stderr_captured or stderr_captured,
            )

    # setup/teardown passed (or other): keep call outcome, extend duration, merge streams
    return TestReportEntry(
        nodeid=existing.nodeid,
        outcome=existing.outcome,
        duration=new_dur,
        err_full=existing.err_full,
        stdout_captured=existing.stdout_captured or stdout_captured,
        stderr_captured=existing.stderr_captured or stderr_captured,
    )


def _initial_entry_from_phase(
    report: pytest.TestReport,
    *,
    full_text: str,
    err_full: str | None,
    stdout_captured: str | None,
    stderr_captured: str | None,
) -> TestReportEntry:
    when = report.when
    duration = float(getattr(report, "duration", 0) or 0)
    if report.outcome == "failed":
        phase_err = err_full or (full_text.strip() if full_text.strip() else None)
        return TestReportEntry(
            nodeid=report.nodeid,
            outcome="failed",
            duration=duration,
            err_full=phase_err,
            stdout_captured=stdout_captured,
            stderr_captured=stderr_captured,
        )
    if when == "call":
        if report.outcome == "skipped":
            return TestReportEntry(
                nodeid=report.nodeid,
                outcome="skipped",
                duration=duration,
                err_full=None,
                stdout_captured=stdout_captured,
                stderr_captured=stderr_captured,
            )
        return TestReportEntry(
            nodeid=report.nodeid,
            outcome="passed",
            duration=duration,
            err_full=None,
            stdout_captured=stdout_captured,
            stderr_captured=stderr_captured,
        )
    # First event is setup/teardown passed: provisional until call runs
    return TestReportEntry(
        nodeid=report.nodeid,
        outcome="passed",
        duration=duration,
        err_full=None,
        stdout_captured=stdout_captured,
        stderr_captured=stderr_captured,
    )


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
def sample_data_root() -> Path:
    """Session-scoped path to sample data files from GitHub."""
    return ensure_sample_data_files_root()


@pytest.fixture(scope="session")
def ai_models_configured(
    pipeshub_client: PipeshubClient,
) -> Generator[SeededAIModel, None, None]:
    """Seed a single OpenAI LLM model and tear it down at session end.

    Required by tests that wait for ``indexingStatus == COMPLETED``: without an
    LLM the indexing pipeline cannot finish, and tests will either time out or
    fail. Mirrors the frontend's ``modelService.addModel`` payload so the same
    backend validation/health-check path runs in tests.

    Reads ``TEST_OPENAI_API_KEY`` (or ``OPENAI_API_KEY``) for the API key and
    optionally ``TEST_OPENAI_LLM_MODEL`` to override the model name. Raises
    ``RuntimeError`` if no API key is available — failure is loud rather than a
    silent indexing timeout downstream.

    On teardown, the seeded model is DELETEd via the same providers endpoint so
    no test residue is left on the backend.
    """
    seeded = setup_test_llm_model(pipeshub_client)
    try:
        yield seeded
    finally:
        teardown_test_llm_model(pipeshub_client, seeded)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def graph_provider(config_service) -> AsyncGenerator["GraphProviderProtocol", None]:
    """
    Session-scoped async graph provider (Neo4j or ArangoDB based on TEST_GRAPH_DB_TYPE).
    
    This provider gives access to all base provider methods plus test-specific
    helper methods (count_records, assert_min_records, etc.).
    
    Usage in tests:
        async def test_something(graph_provider):
            count = await graph_provider.count_records(connector_id)
            await graph_provider.assert_min_records(connector_id, 5)
            
            # Also has all base provider methods
            doc = await provider.get_document("key", "collection")
    """
    from helper.neo4j_integration import TestNeo4jProvider
    from helper.arango_test_provider import TestArangoHTTPProvider
    
    graph_type = os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower()
    
    if graph_type == "arango":
        # Validate ArangoDB env vars
        arango_url = os.getenv("TEST_ARANGO_URL")
        arango_username = os.getenv("TEST_ARANGO_USERNAME")
        arango_password = os.getenv("TEST_ARANGO_PASSWORD")
        
        if not arango_url or not arango_password:
            pytest.skip("TEST_ARANGO_URL / TEST_ARANGO_PASSWORD not set; skipping tests requiring graph_provider.")
        
        provider = TestArangoHTTPProvider(config_service=config_service)
        connected = await provider.connect()
        if not connected:
            pytest.fail("Failed to connect TestArangoHTTPProvider to ArangoDB")
    else:
        # Default to Neo4j
        neo4j_uri = os.getenv("TEST_NEO4J_URI")
        neo4j_user = os.getenv("TEST_NEO4J_USERNAME")
        neo4j_password = os.getenv("TEST_NEO4J_PASSWORD")
        
        if not neo4j_uri or not neo4j_user or not neo4j_password:
            pytest.skip("TEST_NEO4J_URI / TEST_NEO4J_USERNAME / TEST_NEO4J_PASSWORD not set; skipping tests requiring graph_provider.")
        
        provider = TestNeo4jProvider(config_service=config_service)
        connected = await provider.connect()
        if not connected:
            pytest.fail("Failed to connect TestNeo4jProvider to Neo4j")
    
    try:
        yield provider
    finally:
        await provider.disconnect()


def pytest_sessionstart(session) -> None:  # type: ignore[override]
    """
    Pytest hook to validate that critical env vars are present.

    Validates env vars based on TEST_GRAPH_DB_TYPE (neo4j or arango).
    Prod (PIPESHUB_TEST_ENV=prod): require PIPESHUB_BASE_URL, CLIENT_ID, CLIENT_SECRET, and graph DB vars.
    Local (PIPESHUB_TEST_ENV=local): require PIPESHUB_BASE_URL, graph DB vars,
    and either (CLIENT_ID + CLIENT_SECRET) or (PIPESHUB_TEST_USER_EMAIL + PIPESHUB_TEST_USER_PASSWORD).
    """
    test_env = os.getenv("PIPESHUB_TEST_ENV", "").strip().lower()
    graph_type = os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower()
    env_file = ".env.prod" if test_env == "prod" else (".env.local" if test_env == "local" else "none")
    base_url = os.getenv("PIPESHUB_BASE_URL", "")
    log = logging.getLogger("integration-tests")
    log.info(
        "PIPESHUB_TEST_ENV=%s, TEST_GRAPH_DB_TYPE=%s, env file=%s, base_url=%s",
        test_env or "(not set)",
        graph_type,
        env_file,
        base_url or "(not set)",
    )

    missing = []
    is_local = test_env == "local"

    if not os.getenv("PIPESHUB_BASE_URL"):
        missing.append("PIPESHUB_BASE_URL")

    # Validate graph DB vars based on TEST_GRAPH_DB_TYPE
    if graph_type == "arango":
        graph_vars = ["TEST_ARANGO_URL", "TEST_ARANGO_PASSWORD"]
    else:
        graph_vars = ["TEST_NEO4J_URI", "TEST_NEO4J_USERNAME", "TEST_NEO4J_PASSWORD"]

    if is_local:
        for key in graph_vars:
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
        for key in graph_vars:
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
    global _integration_test_reports_by_nodeid, _integration_test_report_order
    _integration_test_reports_by_nodeid = {}
    _integration_test_report_order = []
    config._integration_test_reports_by_nodeid = _integration_test_reports_by_nodeid  # type: ignore[attr-defined]
    config._integration_test_report_order = _integration_test_report_order  # type: ignore[attr-defined]
    config._integration_session_start = time.monotonic()  # type: ignore[attr-defined]


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Collect pass/fail/skip + failure text for HTML report (setup, call, teardown)."""
    if report.when not in ("setup", "call", "teardown"):
        return
    config = getattr(report, "config", None)
    by_nodeid: Dict[str, TestReportEntry] | None = (
        getattr(config, "_integration_test_reports_by_nodeid", None) if config else None
    )
    order: List[str] | None = (
        getattr(config, "_integration_test_report_order", None) if config else None
    )
    if by_nodeid is None:
        by_nodeid = _integration_test_reports_by_nodeid
    if order is None:
        order = _integration_test_report_order

    full_text, err_full, stdout_captured, stderr_captured = _longrepr_and_streams(report)
    nodeid = report.nodeid
    existing = by_nodeid.get(nodeid)
    if existing is None:
        by_nodeid[nodeid] = _initial_entry_from_phase(
            report,
            full_text=full_text,
            err_full=err_full,
            stdout_captured=stdout_captured,
            stderr_captured=stderr_captured,
        )
        order.append(nodeid)
    else:
        by_nodeid[nodeid] = _merge_phase_report(
            existing,
            report,
            full_text=full_text,
            err_full=err_full,
            stdout_captured=stdout_captured,
            stderr_captured=stderr_captured,
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write integration test HTML report under reports/ with timestamp."""
    by_nodeid: Dict[str, TestReportEntry] | None = getattr(
        session.config, "_integration_test_reports_by_nodeid", None,
    )
    order: List[str] | None = getattr(session.config, "_integration_test_report_order", None)
    if by_nodeid is None:
        by_nodeid = _integration_test_reports_by_nodeid
    if order is None:
        order = list(by_nodeid.keys())
    reports: List[TestReportEntry] = [by_nodeid[n] for n in order if n in by_nodeid]
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

