"""Fixtures for the Google Workspace connector behaviour tests (fakes live in google_behaviour_fakes)."""

import types
from typing import Any

import googleapiclient.http
import httplib2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google_behaviour_fakes import (
    TOKEN_URI,
    FakeEntitiesProcessor,
    FakeGoogleHttp,
    FakeSyncPointStore,
)


def _assert_real_google_libraries() -> None:
    """A MagicMock standing in for the Google client would make every test here vacuous."""
    from unittest.mock import MagicMock

    import google.oauth2.service_account
    import googleapiclient.discovery

    for module in (googleapiclient.discovery, googleapiclient.http, google.oauth2.service_account, httplib2):
        assert not isinstance(module, MagicMock), f"{module!r} is a conftest stub, not the real library"
    assert isinstance(googleapiclient.discovery.build, types.FunctionType)


_assert_real_google_libraries()


@pytest.fixture
def google_http(monkeypatch: pytest.MonkeyPatch) -> FakeGoogleHttp:
    http = FakeGoogleHttp()
    http.install(monkeypatch)
    return http


@pytest.fixture(autouse=True)
def backoff_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """googleapiclient's retry backoff is recorded instead of slept."""
    slept: list[float] = []
    monkeypatch.setattr(googleapiclient.http, "time", types.SimpleNamespace(sleep=slept.append))
    return slept


@pytest.fixture
def records() -> FakeEntitiesProcessor:
    return FakeEntitiesProcessor()


@pytest.fixture
def sync_points() -> FakeSyncPointStore:
    return FakeSyncPointStore()


@pytest.fixture(scope="session")
def service_account_info() -> dict[str, Any]:
    """A service-account key the real google-auth signs JWTs with; nothing checks the signature."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "project_id": "behaviour-tests",
        "private_key_id": "key-1",
        "private_key": pem,
        "client_email": "sync@behaviour-tests.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "token_uri": TOKEN_URI,
    }
