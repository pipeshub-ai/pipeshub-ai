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

# Track which top-level packages should be fully mocked (including submodules)
_MOCK_PACKAGE_ROOTS: set = set()


class _MockFinder(importlib.abc.MetaPathFinder):
    """A meta-path finder that intercepts imports for mocked packages and
    auto-creates mock modules for any submodule access."""

    def _matches(self, fullname: str) -> bool:
        for root in _MOCK_PACKAGE_ROOTS:
            if fullname == root or fullname.startswith(root + "."):
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
    except (ImportError, ModuleNotFoundError, RuntimeError, OSError):
        # Remove any partially-loaded submodules to avoid stale state
        to_remove = [k for k in sys.modules if k == name or k.startswith(name + ".")]
        for k in to_remove:
            del sys.modules[k]
        # Register this package root for the meta-path finder
        root = name.split(".")[0]
        _MOCK_PACKAGE_ROOTS.add(root)
        # Install the root mock module immediately
        _mock_finder.load_module(name)


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
