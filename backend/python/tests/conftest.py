"""Shared fixtures for all unit tests."""

import importlib.abc
import importlib.machinery
import logging
import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789abcdef")


def pytest_unconfigure(config):
    """Force-exit the process when running under xdist to prevent hangs
    caused by unawaited async coroutines keeping worker threads alive."""
    if os.environ.get("PYTEST_XDIST_WORKER") or getattr(config.option, "numprocesses", None):
        os._exit(getattr(config, "_exitcode", 0))

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
    except (ImportError, ModuleNotFoundError, RuntimeError, OSError, AttributeError):
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
    "langchain_core",
    "langchain_anthropic",
    "langchain_openai",
    "langchain_google_genai",
    "langchain_aws",
    "langchain_mistralai",
    "langchain_qdrant",
    "litellm",
    "qdrant_client",
    "fastembed",
    "etcd3",
    "docling",
    "docling_core",
    "cv2",
    "spacy",
    "langchain_experimental",
    "openpyxl",
    "celery",
    "aiokafka",
    "github",
    "kiota_abstractions",
    "kiota_authentication_azure",
    "kiota_http",
    "msgraph",
    "msgraph_core",
    "slack_sdk",
    "langchain",
]

for _pkg in _OPTIONAL_PACKAGES:
    _ensure_module(_pkg)


@pytest.fixture
def logger():
    """Provide a silent logger for tests."""
    log = logging.getLogger("test")
    log.setLevel(logging.CRITICAL)
    return log


@pytest.fixture
def mock_graph_provider():
    """Mock IGraphDBProvider with common async methods."""
    provider = AsyncMock()
    provider.get_accessible_virtual_record_ids = AsyncMock(return_value={})
    provider.get_user_by_user_id = AsyncMock(return_value={"email": "test@example.com"})
    provider.get_records_by_record_ids = AsyncMock(return_value=[])
    provider.get_document = AsyncMock(return_value={})
    return provider


@pytest.fixture
def mock_vector_db_service():
    """Mock IVectorDBService."""
    service = AsyncMock()
    service.filter_collection = AsyncMock(return_value=MagicMock())
    service.query_nearest_points = MagicMock(return_value=[])
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
