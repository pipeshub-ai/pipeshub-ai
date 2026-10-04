"""Fixtures for the hand-written SDK tests."""

import os
from collections.abc import Iterator

import pytest
from pipeshub_sdk import Pipeshub, models


@pytest.fixture
def pipeshub() -> Iterator[Pipeshub]:
    with Pipeshub(
        server_url=os.getenv("PIPESHUB_API_URL", "http://localhost:3000/api/v1"),
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
