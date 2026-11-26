"""The integration stack's MongoDB is a replica-set member named ``mongodb:27017``.

A client on the host that discovered the set would be redirected to that name,
which the host cannot resolve, so the default URI talks to the published port
directly.
"""

from __future__ import annotations

import importlib

import helper.config as config


def test_the_default_uri_connects_directly_to_the_published_member(monkeypatch) -> None:
    monkeypatch.delenv("TEST_MONGO_URI", raising=False)
    try:
        uri = importlib.reload(config).MONGO_URI
    finally:
        monkeypatch.undo()
        importlib.reload(config)

    assert uri.startswith("mongodb://admin:password@localhost:27017/")
    assert "directConnection=true" in uri
