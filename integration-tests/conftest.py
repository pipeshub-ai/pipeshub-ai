from __future__ import annotations

import logging
import os
import sys
import time
import warnings
from dataclasses import asdict
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

logging.getLogger("httpx").setLevel(logging.WARNING)

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

    refresh_tokens_env = _THIS_DIR / ".env.refresh_tokens"
    if refresh_tokens_env.exists():
        load_dotenv(dotenv_path=refresh_tokens_env, override=True)


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
    SeededIndexingModels,
    setup_test_indexing_models,
    setup_test_llm_model,
    teardown_test_indexing_models,
    teardown_test_llm_model,
)
from xdist_shared import shared_session_resource  # noqa: E402
from integration_report import TestReportEntry, write_html_report  # noqa: E402
from local_auth import obtain_local_oauth_credentials  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402
from helper.clients.agents_client import AgentsClient  # noqa: E402
from helper.clients.ai_models_client import AIModelsClient  # noqa: E402
from helper.clients.auth_client import AuthClient, UserAccountClient  # noqa: E402
from helper.clients.config_client import ConfigClient  # noqa: E402
from helper.clients.conversations_client import (  # noqa: E402
    AgentConversationsClient,
    ConversationsClient,
)
from helper.clients.kb_client import KBClient  # noqa: E402
from helper.clients.oauth_client import OAuthAppsClient, OAuthProviderClient  # noqa: E402
from helper.clients.org_client import OrgClient  # noqa: E402
from helper.clients.search_client import SearchClient  # noqa: E402
from helper.clients.teams_client import TeamsClient  # noqa: E402
from helper.clients.user_groups_client import UserGroupsClient  # noqa: E402
from helper.clients.users_client import UsersClient  # noqa: E402
from sample_data import ensure_sample_data_files_root  # noqa: E402

# Module-level refs so pytest_runtest_logreport can merge even when report.config is missing
_integration_test_reports_by_nodeid: Dict[str, TestReportEntry] = {}
_integration_test_report_order: List[str] = []

# Set in pytest_configure. pytest_runtest_logreport gets no config argument, and
# only the xdist controller may rewrite node ids (see that hook's docstring).
_IS_XDIST_WORKER = False


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
def teams_client(pipeshub_client: PipeshubClient) -> TeamsClient:
    return TeamsClient(pipeshub_client)


@pytest.fixture(scope="session")
def users_client(pipeshub_client: PipeshubClient) -> UsersClient:
    return UsersClient(pipeshub_client)


@pytest.fixture(scope="session")
def user_groups_client(pipeshub_client: PipeshubClient) -> UserGroupsClient:
    return UserGroupsClient(pipeshub_client)


@pytest.fixture(scope="session")
def org_client(pipeshub_client: PipeshubClient) -> OrgClient:
    return OrgClient(pipeshub_client)


@pytest.fixture(scope="session")
def agents_client(pipeshub_client: PipeshubClient) -> AgentsClient:
    return AgentsClient(pipeshub_client)


@pytest.fixture(scope="session")
def conversations_client(pipeshub_client: PipeshubClient) -> ConversationsClient:
    return ConversationsClient(pipeshub_client)


@pytest.fixture(scope="session")
def agent_conversations_client(
    pipeshub_client: PipeshubClient,
) -> AgentConversationsClient:
    return AgentConversationsClient(pipeshub_client)


@pytest.fixture(scope="session")
def search_client(pipeshub_client: PipeshubClient) -> SearchClient:
    return SearchClient(pipeshub_client)


@pytest.fixture(scope="session")
def auth_client(pipeshub_client: PipeshubClient) -> AuthClient:
    return AuthClient(pipeshub_client)


@pytest.fixture(scope="session")
def user_account_client(pipeshub_client: PipeshubClient) -> UserAccountClient:
    return UserAccountClient(pipeshub_client)


@pytest.fixture(scope="session")
def oauth_provider_client(pipeshub_client: PipeshubClient) -> OAuthProviderClient:
    return OAuthProviderClient(pipeshub_client)


@pytest.fixture(scope="session")
def oauth_apps_client(pipeshub_client: PipeshubClient) -> OAuthAppsClient:
    return OAuthAppsClient(pipeshub_client)


@pytest.fixture(scope="session")
def ai_models_client(pipeshub_client: PipeshubClient) -> AIModelsClient:
    return AIModelsClient(pipeshub_client)


@pytest.fixture(scope="session")
def config_client(pipeshub_client: PipeshubClient) -> ConfigClient:
    return ConfigClient(pipeshub_client)


@pytest.fixture(scope="session")
def kb_client(pipeshub_client: PipeshubClient) -> KBClient:
    return KBClient(pipeshub_client)


@pytest.fixture(scope="session")
def sample_data_root() -> Path:
    """Session-scoped path to sample data files from GitHub."""
    return ensure_sample_data_files_root()


@pytest.fixture(scope="session")
def ai_models_configured(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    pipeshub_client: PipeshubClient,
) -> Generator[SeededAIModel, None, None]:
    """Seed OpenAI LLM + cloud embedding models and tear them down at session end.

    Required by tests that wait for ``indexingStatus == COMPLETED``: without an
    LLM the indexing pipeline cannot finish, and without a cloud embedding the
    backend falls back to the local CPU model (``BAAI/bge-large-en-v1.5``),
    which is slow enough to cause timeouts. Mirrors the frontend's
    ``modelService.addModel`` / onboarding embedding payloads so the same
    backend validation/health-check paths run in tests.

    Reads provider credentials from env (OpenAI, Azure OpenAI, Gemini, or Groq).
    Set ``TEST_AI_MODEL_PROVIDER`` to force a provider. When OpenAI is configured
    but its health check fails (e.g. quota exceeded), the next configured provider
    is tried automatically.

    Raises ``RuntimeError`` if no provider credentials are available or all fail —
    failure is loud rather than a silent indexing timeout downstream.

    Yields the seeded LLM ``SeededAIModel`` for backward compatibility. The
    embedding is also written to org config via the same API call path; indexing
    services load it from Configuration Manager, not from this fixture object.

    On teardown, both models are DELETEd via the providers endpoint so no test
    residue is left on the backend.

    These models are org-wide singletons, so under ``-n`` they are seeded once
    per run and shared by every worker rather than once per worker session.
    """
    with shared_session_resource(
        "ai_models_configured",
        config=request.config,
        tmp_path_factory=tmp_path_factory,
        create=lambda: setup_test_indexing_models(pipeshub_client),
        destroy=lambda models: teardown_test_indexing_models(pipeshub_client, models),
        dump=lambda models: {
            "llm": asdict(models.llm),
            "embedding": asdict(models.embedding),
        },
        load=lambda raw: SeededIndexingModels(
            llm=SeededAIModel(**raw["llm"]),
            embedding=SeededAIModel(**raw["embedding"]),
        ),
    ) as models:
        yield models.llm


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


# Suites that may be spread across xdist workers, as rootdir-relative prefixes.
# Everything else is pinned to one worker by pytest_collection_modifyitems below.
_PARALLEL_SAFE_PATHS = ("response-validation/enterprise-search",)

# Group name is arbitrary; what matters is that every pinned test shares it, so
# ``--dist loadgroup`` routes them all to the same worker.
_SERIAL_XDIST_GROUP = "serial"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: List[pytest.Item],
) -> None:
    """Pin every non-enterprise-search test to one xdist worker.

    The connector suites depend on ``--order-scope=module`` ordering and on
    module-scoped fixtures that drive a whole connector sync, both of which
    assume a single process. Enterprise-search tests hold no shared state --
    each builds its own conversation and the autouse setup only wires clients
    onto ``self`` -- so they are safe to distribute per test.

    Under ``--dist loadgroup`` all tests carrying the same ``xdist_group`` mark
    run on one worker, so this keeps the connectors serial and in order while
    enterprise-search fans out across the remaining workers. No-op on a serial
    run, which leaves ordering identical to today.

    Two xdist details this depends on, both verified against xdist 3.8:

    * ``tryfirst`` is required. xdist's own ``pytest_collection_modifyitems`` in
      ``remote.py`` is what turns the mark into the ``nodeid@group`` suffix the
      scheduler reads, and it otherwise runs before this hook -- marks added
      afterwards are silently ignored and every test spreads.
    * Workers cannot be detected via ``--dist``. ``remote.setup_config`` resets
      ``config.option.dist`` to ``"no"`` on each worker, so a ``dist``-only guard
      no-ops in precisely the processes that matter. ``workerinput`` is the
      reliable signal.
    """
    is_worker = hasattr(config, "workerinput")
    if not is_worker and config.getoption("dist", "no") == "no":
        return

    rootdir = config.rootpath
    for item in items:
        try:
            rel = item.path.relative_to(rootdir).as_posix()
        except ValueError:
            rel = str(item.path)
        if not rel.startswith(_PARALLEL_SAFE_PATHS):
            item.add_marker(pytest.mark.xdist_group(_SERIAL_XDIST_GROUP))


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
    global _IS_XDIST_WORKER
    _IS_XDIST_WORKER = hasattr(config, "workerinput")
    _integration_test_reports_by_nodeid = {}
    _integration_test_report_order = []
    config._integration_test_reports_by_nodeid = _integration_test_reports_by_nodeid  # type: ignore[attr-defined]
    config._integration_test_report_order = _integration_test_report_order  # type: ignore[attr-defined]
    config._integration_session_start = time.monotonic()  # type: ignore[attr-defined]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Collect pass/fail/skip + failure text for HTML report (setup, call, teardown).

    Also drops the ``@<group>`` suffix that xdist appends to node ids under
    ``--dist loadgroup``. That suffix is how its scheduler routes a test to the
    pinned worker, but it is an implementation detail that would otherwise land
    in the JUnit XML, this HTML report, and the failed-test list posted to
    Slack. Only the controller may rewrite it: workers report back by node id,
    so stripping there desynchronises them from the scheduler and tests silently
    go unreported. Scheduling is already settled by the time the controller
    dispatches reports, so this is presentation-only.
    """
    if not _IS_XDIST_WORKER:
        suffix = f"@{_SERIAL_XDIST_GROUP}"
        if report.nodeid.endswith(suffix):
            report.nodeid = report.nodeid[: -len(suffix)]

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

    graph_db = os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower()
    report_path_html = (
        _REPORTS_DIR / f"INTEGRATION_TEST_REPORT_{graph_db}_{timestamp_file}.html"
    )
    write_html_report(
        reports,
        report_path_html,
        timestamp_title=timestamp,
        timestamp_file=timestamp_file,
        env_label=env_label,
        base_url=base_url or "(not set)",
        exitstatus=exitstatus,
        session_wall_s=session_wall_s,
        graph_db=graph_db,
    )

