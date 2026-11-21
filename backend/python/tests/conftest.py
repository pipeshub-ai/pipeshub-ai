"""Shared fixtures for all unit tests."""

import importlib.abc
import importlib.machinery
import importlib.util
import ipaddress
import logging
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import settings
from hypothesis.database import DirectoryBasedExampleDatabase

os.environ.setdefault("SECRET_KEY", "78aa78e734a5383b62cd145ce0e7037fa5496d3b899200385dd1cc44b2f70421")
# LiteLLM fetches its model price map from GitHub when imported unless told to use the copy it ships (H9).
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
# tiktoken downloads an encoding into $TMPDIR/data-gym-cache unless TIKTOKEN_CACHE_DIR holds it. LiteLLM ships
# cl100k_base and o200k_base there but newer releases point tiktoken at them only when its token counter is first
# imported, so on a fresh install every test that counted tokens before that dialled out (H9).
os.environ.setdefault(
    "TIKTOKEN_CACHE_DIR",
    os.path.join(os.path.dirname(importlib.util.find_spec("litellm").origin), "litellm_core_utils", "tokenizers"),
)
# Opik reports its own errors to Sentry; it skips that only when pytest is already imported, which a child process
# a test spawns has not.
os.environ["OPIK_SENTRY_ENABLE"] = "false"
# The agent harness's server setting is on by default (ADR-OPS-06 as amended 2026-11-20); set here too so the suite does
# not depend on the default. A test of the switch, its default or the opt-out sets or deletes it for its own duration.
os.environ.setdefault("AGENT_HARNESS_WORKER_ENABLED", "true")
# The security profile (ADR-OPS-07): unset reads compatibility. Most of this suite was written for the hardened
# defaults, so the process runs as a hardened install; a test of the compatibility profile deletes it for its duration.
os.environ.setdefault("PIPESHUB_HARDENED", "true")

# Importing this configures the root logger (INFO, a stderr handler), as it does in every service. Imported
# here so a narrow selection logs like the full run, whichever test module would have imported it first (FLK3).
import app.utils.logger  # noqa: F401
from app.utils.process_hardening import disable_vendor_telemetry
from tests.support.no_network import NetworkGuard
from tests.support.opt_in_lanes import OPT_IN_LANES

# Before the optional-package probes below load onnxruntime, whose uploader is native and bypasses NetworkGuard.
disable_vendor_telemetry()
# The suite exercises the real metrics backend whatever the edition default.
os.environ.setdefault("TELEMETRY_COLLECTION_FLAG", "true")

# Tests never send traces to a real Opik account. Blank values (not removed ones) also stop a
# later `load_dotenv()` from filling them in from a checkout's `.env`, and the empty config file
# keeps the SDK from reading a developer's `~/.opik.config`.
for _opik_var in ("OPIK_API_KEY", "OPIK_WORKSPACE", "OPIK_URL_OVERRIDE", "OPIK_PROJECT_NAME"):
    os.environ[_opik_var] = ""
os.environ["OPIK_TRACK_DISABLE"] = "true"
_EMPTY_OPIK_CONFIG = os.path.join(tempfile.gettempdir(), f"pipeshub-tests-empty-opik-{getattr(os, "getuid", os.getpid)()}.config")
with open(_EMPTY_OPIK_CONFIG, "w", encoding="utf-8") as _opik_config:
    _opik_config.write("[opik]\n")
os.environ["OPIK_CONFIG_PATH"] = _EMPTY_OPIK_CONFIG

# Installed at import, before the optional-package probes below import anything that might dial.
_network_guard = NetworkGuard.from_env()
if _network_guard is not None:
    _network_guard.install()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    expression = config.getoption("markexpr") or ""
    closed = [lane for lane in OPT_IN_LANES if lane not in expression]
    if not closed:
        return
    kept, deselected = [], []
    for item in items:
        (deselected if any(item.get_closest_marker(lane) for lane in closed) else kept).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


def pytest_configure(config: pytest.Config) -> None:
    if _network_guard is not None:
        _network_guard.register(config)
    if config.pluginmanager.hasplugin("timeout"):
        from tests.support.timeout_backstop import TimeoutBackstop

        config.pluginmanager.register(TimeoutBackstop(), "pxh-timeout-backstop")
    _own_sandbox_objects()


def _own_sandbox_objects() -> None:
    """SBXLEAK: the sandboxes, networks and volumes a broker makes in this run carry this process's and this run's
    labels, and are removed when the session ends or the process does (``tests/support/sandbox_reaper.py``)."""
    from tests.support import docker_session, sandbox_reaper

    _ = sandbox_reaper.start_owner()
    try:
        sandbox_reaper.label_broker_objects()
    except ImportError:
        return
    docker_session.at_exit(sandbox_reaper.finalize)
    if sandbox_reaper.owns_run():
        sandbox_reaper.install_sigterm()
        sandbox_reaper.sweep_dead_runs_in_background()

# TEST-STRATEGY §2: PRs replay the same examples; the nightly lane (`--hypothesis-profile=nightly`) searches
# wider and keeps what it found, uploaded when it fails. A test that pins `max_examples` opts out of both.
settings.register_profile("ci", derandomize=True, max_examples=200, deadline=None, database=None, print_blob=True)
settings.register_profile(
    "nightly",
    max_examples=5_000,
    deadline=None,
    database=DirectoryBasedExampleDatabase(".hypothesis/examples"),
    print_blob=True,
)
settings.load_profile("ci")


@pytest.fixture(autouse=True)
def _fresh_lane_assignments(monkeypatch: pytest.MonkeyPatch) -> None:
    # The lane maps producers share are process-wide. One a test left behind
    # made every later stale-recovery pass run lane upkeep against a Redis
    # that is not there, seconds per pass.
    from app.services.messaging.lanes import assignment

    monkeypatch.setattr(assignment, "_shared", {})


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # The forced exit below must report the run's own outcome.
    session.config._exitcode = int(exitstatus)
    # Last, so xdist's workers are down before the run's own process removes what any of them left (SBXLEAK).
    from tests.support import sandbox_reaper

    sandbox_reaper.finalize()


def pytest_unconfigure(config):
    """Force-exit the process when running under xdist to prevent hangs
    caused by unawaited async coroutines keeping worker threads alive."""
    if os.environ.get("PYTEST_XDIST_WORKER") or getattr(config.option, "numprocesses", None):
        from tests.support import docker_session

        # os._exit skips atexit: what a test process registered to remove at exit is removed here (SBXLEAK).
        docker_session.run_exit_handlers()
        # os._exit skips the interpreter's own flush, which dropped the summary.
        sys.stdout.flush()
        sys.stderr.flush()
        # A run that never reached sessionfinish did not pass.
        os._exit(getattr(config, "_exitcode", pytest.ExitCode.INTERNAL_ERROR))

# ---------------------------------------------------------------------------
# Auto-mock optional third-party modules that may not be installed in the
# test environment.  This runs at *import time* — before any test module or
# application code is collected — so that ``from arango import ArangoClient``
# and similar top-level imports succeed even when the package isn't present.
# ---------------------------------------------------------------------------

# Track the EXACT dotted names that should be mocked. Any submodule of an
# explicitly-mocked name is also covered, but the mock is NEVER extended up
# to a broader root. That is critical: ``azure.storage.fileshare`` being
# unavailable must not cause real ``azure.identity`` imports to be
# intercepted by the mock layer.
_MOCK_PACKAGE_NAMES: set = set()


class _MockFinder(importlib.abc.MetaPathFinder):
    """A meta-path finder that intercepts imports for mocked packages and
    auto-creates mock modules for any submodule access."""

    def _matches(self, fullname: str) -> bool:
        for name in _MOCK_PACKAGE_NAMES:
            if fullname == name or fullname.startswith(name + "."):
                return True
        return False

    def find_spec(self, fullname, path, target=None):
        if not self._matches(fullname):
            return None
        # Ensure mock is installed in sys.modules
        self.load_module(fullname)
        return importlib.machinery.ModuleSpec(fullname, self, is_package=True)

    def create_module(self, spec):
        return sys.modules.get(spec.name)

    def exec_module(self, module):
        pass

    def find_module(self, fullname, path=None):
        if self._matches(fullname):
            return self
        return None

    def load_module(self, fullname):
        if fullname in sys.modules:
            return sys.modules[fullname]
        mod = MagicMock()
        mod.__path__ = []
        mod.__name__ = fullname
        mod.__spec__ = importlib.machinery.ModuleSpec(fullname, self)
        mod.__file__ = None
        mod.__loader__ = self
        mod.__package__ = fullname
        sys.modules[fullname] = mod
        return mod


_mock_finder = _MockFinder()
sys.meta_path.insert(0, _mock_finder)


def _ensure_module(name: str) -> None:
    """Insert a MagicMock into sys.modules for *name* if it is not importable."""
    if name in sys.modules:
        return
    try:
        __import__(name)
    except (
        ImportError,
        ModuleNotFoundError,
        RuntimeError,
        OSError,
        AttributeError,
        TypeError,
    ):
        # Remove any partially-loaded submodules to avoid stale state
        to_remove = [k for k in sys.modules if k == name or k.startswith(name + ".")]
        for k in to_remove:
            del sys.modules[k]
        # Register this exact dotted name (and, to make ``import X.Y`` work,
        # every parent package that was not already importable) so the
        # meta-path finder can intercept subsequent imports. We intentionally
        # do NOT hoist up to the root because doing so would make the finder
        # intercept unrelated sibling subpackages (e.g. mocking
        # ``azure.storage.fileshare`` must not mask real ``azure.identity``).
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            dotted = ".".join(parts[:i])
            try:
                __import__(dotted)
            except Exception:  # pragma: no cover - defensive
                _MOCK_PACKAGE_NAMES.add(dotted)
                _mock_finder.load_module(dotted)


# Only mock packages that may genuinely be absent in the test environment.
# Do NOT mock packages that are installed — it breaks their real behavior.
_OPTIONAL_PACKAGES = [
    "arango",
    "torch",
    "safetensors",
    "dropbox",
    "google.cloud",
    "google.cloud.storage",
    "azure.storage.fileshare",
    "sentence_transformers",
    # LangChain*: declared in pyproject — must not be MagicMock stubs or
    # isinstance(..., ChatOpenAI) and PydanticOutputParser break in unit tests.
    "litellm",
    "qdrant_client",
    "fastembed",
    "etcd3",
    "docling",
    "docling_core",
    "chardet",
    "talon",
    "crawl4ai",
    "cv2",
    "spacy",
    "openpyxl",
    "pdfplumber",
    "celery",
    "aiokafka",
    "github",
    "kiota_abstractions",
    "kiota_authentication_azure",
    "kiota_http",
    "msgraph",
    "msgraph_core",
    "slack_sdk",
    "selectolax",
    "trafilatura",
]

# docling_parse embeds a native C extension (pdf_parsers) that terminates the
# process with STATUS_ENTRYPOINT_NOT_FOUND on some Windows builds.  Python's
# exception system cannot intercept a fatal OS-level exit, so _ensure_module()
# would crash rather than mock.  Register the entire docling_parse namespace as
# a mock *before* _ensure_module("docling") runs, so the MockFinder intercepts
# every ``from docling_parse.xxx import ...`` before the native DLL is touched.
_MOCK_PACKAGE_NAMES.add("docling_parse")
_mock_finder.load_module("docling_parse")

# talon imports cchardet, which has no Python 3.12 build and is excluded from installs
# ([tool.uv] in pyproject.toml). Production aliases it to chardet before importing talon
# (gmail/talon_utils.py); without the same alias here the talon probe below fails and
# every Gmail test silently runs against a MagicMock talon.
try:
    import cchardet  # noqa: F401
except ImportError:
    try:
        import chardet as _chardet
    except ImportError:
        _chardet = None
    if _chardet is not None:
        sys.modules["cchardet"] = _chardet

for _pkg in _OPTIONAL_PACKAGES:
    _ensure_module(_pkg)

# ``docling`` may be installed as an incomplete stub/namespace (importable root
# but missing datamodel/document_converter). Force-mock the submodules production
# code needs so connector/query unit tests can import without a full docling install.
try:
    __import__("docling.datamodel.document")
    __import__("docling.document_converter")
except Exception:
    for _dotted in (
        "docling",
        "docling.datamodel",
        "docling.datamodel.document",
        "docling.document_converter",
    ):
        _MOCK_PACKAGE_NAMES.add(_dotted)
        for _k in [k for k in list(sys.modules) if k == _dotted or k.startswith(_dotted + ".")]:
            del sys.modules[_k]
        _mock_finder.load_module(_dotted)


# ---------------------------------------------------------------------------
# Inject a minimal Document stub into the mocked langchain_core.documents so
# that production code calling Document(page_content=..., metadata=...) and
# tests using isinstance(c, Document) / c.page_content both work correctly
# even when langchain_core is not installed.
# ---------------------------------------------------------------------------

class _DocumentStub:
    """Minimal langchain_core Document stub for tests."""

    def __init__(self, page_content: str = "", metadata=None, **kwargs):
        self.page_content = page_content
        self.metadata = metadata if metadata is not None else {}

    def __repr__(self) -> str:
        return f"Document(page_content={self.page_content!r})"


def _inject_document_stub() -> None:
    import types

    # When langchain_core is installed, use the real documents module so
    # transitive imports (e.g. langchain_aws -> BaseDocumentCompressor) work.
    try:
        import langchain_core  # noqa: F401
        return
    except ImportError:
        pass

    for mod_name in ("langchain_core.documents", "langchain_core.documents.base"):
        if mod_name not in sys.modules:
            mod = types.ModuleType(mod_name)
            mod.__path__ = []  # type: ignore[attr-defined]
            sys.modules[mod_name] = mod
        sys.modules[mod_name].Document = _DocumentStub  # type: ignore[attr-defined]


_inject_document_stub()


@pytest.fixture(autouse=True)
def _toolset_registry_kept() -> Iterator[None]:
    """Every test gets the process-wide ToolsetRegistry back as it found it (instance and toolsets): tests that
    replace, clear or fill it otherwise decide what every later test in that xdist worker sees (H5: ET-3 read 5 fake
    toolsets, NE-1 read none). Not imported before the test: it was empty, and is left empty (same instance, which
    modules imported during the test may hold)."""
    module = sys.modules.get("app.agents.registry.toolset_registry")
    if module is None:
        yield
        module = sys.modules.get("app.agents.registry.toolset_registry")
        instance = None if module is None else module.ToolsetRegistry._instance
        if instance is not None and getattr(instance, "_initialized", False):
            instance._toolsets = {}
        return
    from tests.support.toolset_registry import toolset_registry_restored

    with toolset_registry_restored():
        yield


@pytest.fixture(autouse=True)
def _opik_client_ended() -> Iterator[None]:
    """An Opik client a test built is ended after it: its threads otherwise dial Opik Cloud for the rest of the
    worker, and a later test that records connects saw them (H8: test_model_egress)."""
    from tests.support.opik_client import opik_global_client_ended

    with opik_global_client_ended():
        yield


@pytest.fixture(autouse=True)
def _sse_shutdown_state_is_per_test() -> Iterator[None]:
    """sse_starlette ends every SSE stream in the process once a uvicorn server it watched saw an exit
    (``AppStatus.should_exit``, a process-wide flag nothing resets). A test that stops a server must not end a
    later test's MCP streams: they came back cut ("ASGI callable returned without completing response")."""
    yield
    sse = sys.modules.get("sse_starlette.sse")
    if sse is not None:
        sse.AppStatus.should_exit = False


@pytest.fixture(autouse=True)
def _reset_default_backpressure_coordinator():
    """ParsingClient/DoclingClient default to a process-wide
    BackpressureCoordinator singleton when the caller doesn't pass one
    explicitly (see app.services.messaging.backpressure) — without a reset,
    a 429 signalled in one test's client would leak a pause into unrelated
    tests constructed later in the same worker process."""
    from app.services.messaging.backpressure import set_default_backpressure_coordinator
    set_default_backpressure_coordinator(None)
    yield
    set_default_backpressure_coordinator(None)


@pytest.fixture(autouse=True)
def _reset_narrow_access_memo() -> Iterator[None]:
    """Search remembers, per process, scopes its container filter could not
    serve. A scope marked in one test must not route another test's search."""
    retrieval = sys.modules.get("app.modules.retrieval.retrieval_service")
    if retrieval is not None:
        retrieval.narrow_access_memo.clear()
    yield
    retrieval = sys.modules.get("app.modules.retrieval.retrieval_service")
    if retrieval is not None:
        retrieval.narrow_access_memo.clear()


@pytest.fixture(autouse=True)
def _no_dns_for_model_endpoints(monkeypatch):
    """A model health check looks its endpoint's name up before calling it; unit tests must
    not reach a resolver, so every name resolves to a public address. Tests of the lookup
    set their own answers."""
    aimodels = sys.modules.get("app.utils.aimodels")
    if aimodels is not None:
        monkeypatch.setattr(aimodels, "_resolved_addresses", lambda host: [ipaddress.ip_address("93.184.216.34")])


@pytest.fixture
def flow_guard_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The durable loop with the flow guard off (UX1). For rigs that compare it with today's loop or replay
    recordings made without the guard: the guard is a deliberate difference (an external write after a read
    waits for a person), tested on its own in test_ux1_flow_default.py and the SEC1/SEC2 flow tests."""
    monkeypatch.setenv("HARNESS_FLOW_GUARD", "false")


# MG-15: every model built through ``app/utils/llm.py`` asks the placement verdicts first. The unit and eval trees
# test something else, so they run where no placement rule refuses a call; the other trees bring their own.
_PUBLISHED_PLACEMENT_TREES = frozenset({"unit", "evals"})
_TESTS_DIR = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _published_placement_in_unit_and_eval_trees(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A module that tests placement itself sets ``PLACEMENT_UNDER_TEST = True`` and gives its own document."""
    try:
        tree = request.path.resolve().relative_to(_TESTS_DIR).parts[0]
    except ValueError:
        return
    if tree in _PUBLISHED_PLACEMENT_TREES and not getattr(request.module, "PLACEMENT_UNDER_TEST", False):
        from tests.support.model_placement import no_placement_pin

        no_placement_pin(monkeypatch)


@pytest.fixture(autouse=True)
def _followups_off(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Suggested next questions (UX5) are one more model call after every answered top-level run; scripted models,
    the parity rigs and the replay corpora script none, so tests run without them unless marked ``followups``
    (test_ux5_followups.py and the assembler's wiring test)."""
    if request.node.get_closest_marker("followups") is not None:
        return
    # By import path: a test that imports the assembler only inside its body (a durable run first in its worker)
    # must still see the switch off.
    monkeypatch.setattr("app.harness.assembly.assembler.FOLLOWUP_SUGGESTIONS", False)


# The harness switch as the code reads it, kept before the session-wide patch below.
_REAL_HARNESS_ENABLED: list[Callable[[], bool]] = []


@pytest.fixture(scope="session", autouse=True)
def _mcp_hub_default_off() -> Iterator[None]:
    """D-MCP-1 turned the MCP hub on for harness runs wherever the harness is. Tests written before it say "hub off"
    by leaving ``MCP_HUB`` unset, harness assembly tests included, so an unset flag stays off here unless a test is
    marked ``mcp_hub_default`` (``test_dmcp1_baseline.py``, ``test_dmcp1_surface.py``); a test that sets ``MCP_HUB``
    gets what it set. No environment is changed, so no process a test starts sees a different harness switch.

    Session-wide, so a module- or class-scoped fixture that drives a run before any test's own fixtures (the
    long-task rig's) sees the same default as the tests do."""
    from app.harness.mcp_hub import settings as hub_settings

    _REAL_HARNESS_ENABLED.append(hub_settings.harness_enabled)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(hub_settings, "harness_enabled", lambda: False)
        yield


@pytest.fixture(autouse=True)
def _mcp_hub_real_default(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A test marked ``mcp_hub_default`` reads the code's real harness switch."""
    if request.node.get_closest_marker("mcp_hub_default") is None:
        return
    monkeypatch.setattr("app.harness.mcp_hub.settings.harness_enabled", _REAL_HARNESS_ENABLED[0])


@pytest.fixture
def logger():
    """Provide a silent logger for tests."""
    log = logging.getLogger("test")
    previous_level = log.level
    log.setLevel(logging.CRITICAL)
    yield log
    # "test" is the parent of every test.* logger, so a level left behind here
    # silences them in every later test, and caplog then sees nothing.
    log.setLevel(previous_level)


@pytest.fixture
def mock_graph_provider():
    """Mock IGraphDBProvider with common async methods."""
    from app.services.graph_db.interface.graph_db_provider import (
        AccessCheck,
        AccessibleContainers,
    )

    provider = AsyncMock()
    provider.get_accessible_virtual_record_ids = AsyncMock(return_value={})
    provider.get_user_by_user_id = AsyncMock(return_value={"email": "test@example.com"})
    provider.get_records_by_record_ids = AsyncMock(return_value=[])
    provider.get_document = AsyncMock(return_value={})
    # Stubbed explicitly rather than left to AsyncMock's auto-children: those
    # return a MagicMock, whose `fallback_reason` is truthy and whose sets are
    # empty, so the container path would take an arbitrary branch instead of an
    # obviously-unstubbed one. A test that wants containers overrides these.
    provider.get_accessible_containers = AsyncMock(
        return_value=AccessibleContainers(fallback_reason="not stubbed in this test")
    )
    # Admits every node it is asked about; a test of a denial stubs it.
    provider.check_access = AsyncMock(
        side_effect=lambda _user_key, _org_id, *, node_ids=(), **_kwargs: AccessCheck(node_ids=frozenset(node_ids)),
    )
    return provider


@pytest.fixture
def mock_vector_db_service():
    """Mock IVectorDBService."""
    from unittest.mock import MagicMock as _MagicMock
    service = AsyncMock()
    service.filter_collection = AsyncMock(return_value=_MagicMock())
    service.query_nearest_points = AsyncMock(return_value=[])
    # get_capabilities and get_service_name are synchronous in the interface
    try:
        from app.services.vector_db.models import VectorDBCapabilities
        caps = VectorDBCapabilities(
            supports_sparse_vectors=False,
            supports_server_side_text_search=False,
        )
    except Exception:
        caps = _MagicMock()
        caps.supports_sparse_vectors = False
        caps.supports_server_side_text_search = False
    service.get_capabilities = _MagicMock(return_value=caps)
    service.get_service_name = _MagicMock(return_value="mock")
    service.transient_search_error = _MagicMock(return_value=None)
    return service


@pytest.fixture
def mock_config_service():
    """Mock ConfigurationService."""
    service = AsyncMock()
    service.get_config = AsyncMock(return_value={
        "llm": [{"provider": "openai", "isDefault": True, "configuration": {"model": "gpt-4"}}],
        "embedding": [{"provider": "openai", "isDefault": True, "configuration": {"model": "text-embedding-3-small"}}],
    })
    return service


@pytest.fixture
def mock_blob_store():
    """Mock BlobStorage."""
    return MagicMock()
