import os
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent
_HELPER_DIR = _THIS_DIR / "helper"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

from pipeshub_client import PipeshubClient  # noqa: E402


def _load_env() -> None:
    """Load environment variables from the local .env in this folder, if present."""
    env_path = _THIS_DIR / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)


def _export_remote_neo4j_env() -> None:
    """
    Ensure Neo4j-related env vars point at the remote Aura instance for tests.

    We keep the TEST_NEO4J_* names in integration-tests/.env and mirror them into the
    generic NEO4J_* names expected by backend code, so no local Neo4j is used.
    """
    uri = os.getenv("TEST_NEO4J_URI")
    user = os.getenv("TEST_NEO4J_USERNAME")
    password = os.getenv("TEST_NEO4J_PASSWORD")
    database = os.getenv("TEST_NEO4J_DATABASE", "neo4j")

    if uri:
        os.environ["NEO4J_URI"] = uri
    if user:
        os.environ["NEO4J_USERNAME"] = user
    if password:
        os.environ["NEO4J_PASSWORD"] = password
    if database:
        os.environ["NEO4J_DATABASE"] = database


def _init_global_test_env() -> None:
    """
    Initialize all shared test environment concerns:
    - Load integration-tests/.env
    - Point Neo4j env at remote Aura instance (TEST_NEO4J_*)
    """
    _load_env()
    _export_remote_neo4j_env()


_init_global_test_env()


def get_pipeshub_client() -> PipeshubClient:
    """Convenience helper for tests that prefer direct construction."""
    return PipeshubClient()


def pytest_sessionstart(session) -> None:  # type: ignore[override]
    """
    Pytest hook to validate that critical remote env vars are present.

    If they are missing, we keep tests importable but make failures explicit early.
    """
    missing = []
    for key in ["PIPESHUB_BASE_URL", "PIPESHUB_USER_BEARER_TOKEN"]:
        if not os.getenv(key):
            missing.append(key)
    for key in ["TEST_NEO4J_URI", "TEST_NEO4J_USERNAME", "TEST_NEO4J_PASSWORD"]:
        if not os.getenv(key):
            missing.append(key)

    if missing:
        warnings.warn(
            f"Missing remote integration env vars: {', '.join(sorted(set(missing)))}",
            UserWarning,
            stacklevel=2,
        )

