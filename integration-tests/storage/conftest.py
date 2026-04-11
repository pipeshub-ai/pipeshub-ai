"""Fixtures for storage integration tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _THIS_DIR.parent
_HELPER_DIR = _ROOT_DIR / "helper"
for _p in (_ROOT_DIR, _HELPER_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv


def _load_env() -> None:
    env_path = _ROOT_DIR / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    test_env = os.getenv("PIPESHUB_TEST_ENV", "").strip().lower()
    if test_env == "local":
        local_env = _ROOT_DIR / ".env.local"
        if local_env.exists():
            load_dotenv(dotenv_path=local_env, override=True)
        os.environ.pop("PIPESHUB_USER_BEARER_TOKEN", None)
    elif test_env == "prod":
        prod_env = _ROOT_DIR / ".env.prod"
        if prod_env.exists():
            load_dotenv(dotenv_path=prod_env, override=True)


_load_env()

from local_auth import obtain_local_oauth_credentials
from pipeshub_client import PipeshubClient
from storage_client import StorageClient


@pytest.fixture(scope="session", autouse=True)
def local_oauth_credentials() -> None:
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


@pytest.fixture(scope="session")
def pipeshub_client() -> PipeshubClient:
    return PipeshubClient()


@pytest.fixture(scope="session")
def sc(pipeshub_client: PipeshubClient) -> StorageClient:
    return StorageClient(pipeshub_client)
