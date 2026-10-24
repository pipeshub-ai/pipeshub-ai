"""Fixtures for the hand-written SDK tests."""

import os
from collections.abc import Iterator

import pytest
from pipeshub_sdk import Pipeshub, models

SERVER_URL = os.getenv("PIPESHUB_API_URL", "http://localhost:3000/api/v1")
# The SDK's default is httpx's 5 s per read; an agent's answer can take longer.
TIMEOUT_MS = 300_000


def bearer_client(token: str) -> Pipeshub:
    """A client that sends `token` as the bearer: a session JWT or a personal access token."""
    return Pipeshub(
        server_url=SERVER_URL,
        timeout_ms=TIMEOUT_MS,
        security=models.Security(bearer_auth=token),
    )


@pytest.fixture
def pipeshub() -> Iterator[Pipeshub]:
    with Pipeshub(
        server_url=SERVER_URL,
        timeout_ms=TIMEOUT_MS,
        security=models.Security(
            oauth2=models.SchemeOauth2(
                client_id=os.environ["PIPESHUB_CLIENT_ID"],
                client_secret=os.environ["PIPESHUB_CLIENT_SECRET"],
                token_url=os.getenv(
                    "PIPESHUB_TOKEN_URL", "http://localhost:3000/api/v1/oauth2/token"
                ),
            ),
        ),
    ) as client:
        yield client


@pytest.fixture
def session() -> Iterator[Pipeshub]:
    """The OAuth app and personal access token routes accept only an interactive session."""
    with bearer_client(os.environ["PIPESHUB_SESSION_TOKEN"]) as client:
        yield client
