"""The demo sync waits for the sign-in personas before it writes memberships.

Accounts land in the graph a second or two after the API call that creates
them, while enabling the connector reaches the sync straight away. Group
membership silently skips a member the graph does not have yet, so the wait is
what keeps a first-run demo from leaving Alice and Bob out of every group.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.connectors.sources.demo.connector import _wait_for_members

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_returns_once_every_account_exists() -> None:
    present = {"alice@acme-demo.example", "bob@acme-demo.example"}

    async def lookup(email: str) -> object | None:
        return object() if email in present else None

    await _wait_for_members(sorted(present), lookup, LOGGER, timeout=1.0, poll=0.01)


@pytest.mark.asyncio
async def test_waits_for_an_account_that_arrives_late() -> None:
    calls: list[str] = []

    async def lookup(email: str) -> object | None:
        calls.append(email)
        return object() if calls.count(email) > 2 else None

    await _wait_for_members(["bob@acme-demo.example"], lookup, LOGGER, timeout=2.0, poll=0.01)

    assert calls.count("bob@acme-demo.example") == 3


@pytest.mark.asyncio
async def test_fails_the_sync_when_an_account_never_arrives() -> None:
    async def lookup(_email: str) -> object | None:
        return None

    with pytest.raises(RuntimeError) as failure:
        await _wait_for_members(
            ["alice@acme-demo.example"], lookup, LOGGER, timeout=0.05, poll=0.01
        )

    assert "alice@acme-demo.example" in str(failure.value)


@pytest.mark.asyncio
async def test_an_empty_list_does_not_wait() -> None:
    async def lookup(_email: str) -> object | None:  # pragma: no cover - never called
        raise AssertionError("no account should be looked up")

    await asyncio.wait_for(_wait_for_members([], lookup, LOGGER), timeout=1.0)
