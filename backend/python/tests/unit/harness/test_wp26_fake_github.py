"""WP-26 (UI-4): the e2e lanes' fake GitHub answers the repository picker's reads as GitHub does.

The server is the real one (``deployment/e2e/fake_saas/server.py``) under an ASGI transport, read by the product's
own listing (``app.agents.actions.github.repository_picker``) with the token the human stack seeds.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agents.actions.github.repository_picker import (
    GitHubListingFailed,
    list_branches,
    list_repositories,
)

SERVER = Path(__file__).resolve().parents[5] / "deployment" / "e2e" / "fake_saas" / "server.py"
TOKEN = "pxh-fake-github-token"
API = "http://fake-saas/api/v3"


def _load() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ.setdefault("FAKE_SAAS_CONTROL_TOKEN", "control")
    os.environ["FAKE_SAAS_GITHUB_TOKEN"] = TOKEN
    spec = importlib.util.spec_from_file_location("wp26_fake_saas_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load()


def _transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=server.app())


async def test_wp26_fake_github_lists_the_seeded_repositories_and_branches() -> None:
    listing = await list_repositories(TOKEN, search="widgets", api=API, transport=_transport())
    assert [(r["fullName"], r["private"], r["url"]) for r in listing.repositories] == [
        ("acme/widgets", True, "https://github.com/acme/widgets"),
        ("acme/widgets-docs", False, "https://github.com/acme/widgets-docs"),
    ]
    branches = await list_branches(TOKEN, "acme/widgets", api=API, transport=_transport())
    assert branches == [{"name": "main", "default": True}, {"name": "release/1.x", "default": False}]


async def test_wp26_fake_github_refuses_a_wrong_token() -> None:
    with pytest.raises(GitHubListingFailed) as refused:
        await list_repositories("wrong", api=API, transport=_transport())
    assert refused.value.status == 401
