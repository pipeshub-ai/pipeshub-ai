"""Set an org's Labs feature flag for a test session, and put it back afterwards.

Platform settings are one document: a write replaces every flag and the upload
size cap, so a flag is set by reading the document, changing one key and
writing the whole of it back.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import requests

from helper.pipeshub_client import PipeshubClient


def _settings_url(client: PipeshubClient) -> str:
    return f"{client.base_url}/api/v1/configurationManager/platform/settings"


def read_platform_settings(client: PipeshubClient) -> dict:
    resp = requests.get(_settings_url(client), headers=client._headers(), timeout=client.timeout_seconds)
    assert resp.status_code == 200, f"read platform settings: {resp.status_code}: {resp.text[:300]}"
    return resp.json()


def write_feature_flag(client: PipeshubClient, name: str, value: bool | None) -> None:
    """``None`` removes the flag, which leaves it at its default."""
    settings = read_platform_settings(client)
    flags = dict(settings.get("featureFlags") or {})
    if value is None:
        flags.pop(name, None)
    else:
        flags[name] = value
    body = {"fileUploadMaxSizeBytes": settings.get("fileUploadMaxSizeBytes"), "featureFlags": flags}
    if body["fileUploadMaxSizeBytes"] is None:
        body.pop("fileUploadMaxSizeBytes")
    resp = requests.post(_settings_url(client), headers=client._headers(), json=body, timeout=client.timeout_seconds)
    assert resp.status_code in (200, 201), f"write flag {name}: {resp.status_code}: {resp.text[:300]}"


@contextlib.contextmanager
def feature_flag(client: PipeshubClient, name: str, value: bool) -> Iterator[None]:
    previous = (read_platform_settings(client).get("featureFlags") or {}).get(name)
    write_feature_flag(client, name, value)
    try:
        yield
    finally:
        write_feature_flag(client, name, previous)
