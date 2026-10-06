"""Shared fixtures for the strict OpenAPI audit of /api/v1/configurationManager."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.config_client import ConfigClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from configuration_manager_audit_support import (  # noqa: E402
    GuardSavedConfig,
    MetricsCollectionConfig,
    SeedSlackBot,
    forget_stored_config,
    slack_bot_body,
)


@pytest.fixture
def guard_saved_config(config_client: ConfigClient) -> Iterator[GuardSavedConfig]:
    """Factory: snapshot a saved config before a test overwrites it; it is put back afterwards.

    ``guard_saved_config(sub_path, kv_path, derived=())`` returns what ``GET sub_path``
    answers now. ``derived`` names the fields that GET adds or computes and POST does not
    take. On teardown a config that existed is posted back; one that did not is removed
    from the key-value store at ``kv_path``, because no route deletes it.
    """
    guarded: list[tuple[str, str, tuple[str, ...], dict[str, Any]]] = []

    def _guard(sub_path: str, kv_path: str, derived: tuple[str, ...] = ()) -> dict[str, Any]:
        resp = config_client.get(sub_path)
        assert resp.status_code == 200, f"could not read {sub_path}: {resp.status_code} {resp.text[:300]}"
        before: dict[str, Any] = resp.json()
        guarded.append((sub_path, kv_path, derived, before))
        return dict(before)

    try:
        yield _guard
    finally:
        failures = []
        for sub_path, kv_path, derived, before in reversed(guarded):
            now = config_client.get(sub_path)
            if now.status_code == 200 and now.json() == before:
                continue
            saved = {key: value for key, value in before.items() if key not in derived}
            if saved:
                restored = config_client.post(sub_path, json=saved)
                if restored.status_code != 200:
                    failures.append(f"POST {sub_path}: {restored.status_code} {restored.text[:200]}")
                    continue
            else:
                forget_stored_config(kv_path)
            after = config_client.get(sub_path)
            if after.status_code != 200 or after.json() != before:
                failures.append(f"GET {sub_path} after restore: {after.status_code} {after.text[:200]}")
        assert not failures, f"saved configs not restored: {failures}"


@pytest.fixture
def seed_slack_bot(config_client: ConfigClient) -> Iterator[SeedSlackBot]:
    """Factory: create one Slack bot config and return it; every one is removed on teardown.

    ``seed_slack_bot(**overrides)`` posts ``slack_bot_body(**overrides)`` and returns the
    ``config`` object of the reply (``id``, ``name``, masked ``botToken`` / ``signingSecret``...).
    """
    created: list[str] = []

    def _seed(**overrides: Any) -> dict[str, Any]:
        resp = config_client.post("/slack-bot", json=slack_bot_body(**overrides))
        assert resp.status_code == 200, f"could not seed a Slack bot config: {resp.status_code} {resp.text[:300]}"
        config: dict[str, Any] = resp.json()["config"]
        created.append(config["id"])
        return config

    try:
        yield _seed
    finally:
        leftovers = []
        for config_id in created:
            resp = config_client.delete(f"/slack-bot/{config_id}")
            # 404: the test deleted it itself.
            if resp.status_code not in (200, 404):
                leftovers.append(f"{config_id}: {resp.status_code} {resp.text[:200]}")
        assert not leftovers, f"Slack bot configs left behind: {leftovers}"


@pytest.fixture
def metrics_collection_snapshot(config_client: ConfigClient) -> Iterator[MetricsCollectionConfig]:
    """The stored metrics config before the test; the keys it held are written back afterwards.

    A key that was absent cannot be removed again (no route deletes one), so a test may
    only change a key this snapshot contains; otherwise send a value the store already implies.
    """
    resp = config_client.get("/metricsCollection")
    assert resp.status_code == 200, f"could not read metrics config: {resp.status_code} {resp.text[:300]}"
    before: MetricsCollectionConfig = resp.json()
    try:
        yield dict(before)
    finally:
        # The store keeps the toggle and the interval as strings; the routes take a bool and a number.
        writes: list[tuple[str, str, dict[str, Any]]] = []
        if "enableMetricCollection" in before:
            enabled = before["enableMetricCollection"] not in (False, "false")
            writes.append(("PUT", "/metricsCollection/toggle", {"enableMetricCollection": enabled}))
        if "pushIntervalMs" in before:
            writes.append(
                ("PATCH", "/metricsCollection/pushInterval", {"pushIntervalMs": int(before["pushIntervalMs"])})
            )
        if "serverUrl" in before:
            writes.append(("PATCH", "/metricsCollection/serverUrl", {"serverUrl": before["serverUrl"]}))
        failures = []
        for method, path, body in writes:
            send = config_client.put if method == "PUT" else config_client.patch
            restored = send(path, json=body)
            if restored.status_code != 200:
                failures.append(f"{method} {path}: {restored.status_code} {restored.text[:200]}")
        assert not failures, f"metrics config not restored: {failures}"
