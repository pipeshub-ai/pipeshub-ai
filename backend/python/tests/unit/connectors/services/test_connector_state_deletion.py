"""A deleted connector's own config-store keys go with it, not only its credentials."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.connectors.services.storage_release import delete_connector_credentials
from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.connectors.sources.slack.team.connector import SlackConnector


class Config:
    def __init__(self, kv: dict[str, object], kept: frozenset[str] = frozenset()) -> None:
        self.kv = dict(kv)
        self.kept = kept

    async def get_config(self, key, default=None, **_kw) -> object:
        return self.kv.get(key, default)

    async def delete_config(self, key) -> bool:
        if key in self.kept:
            return False
        return self.kv.pop(key, None) is not None

    async def list_keys_in_directory(self, directory) -> list[str]:
        return [k for k in self.kv if k.startswith(directory)]


def _pending(connector_id: str, channel: str) -> str:
    return f"/services/connectors/{connector_id}/slack_pending_threads/{channel}"


async def test_slack_pending_threads_go_with_the_connector() -> None:
    other = {_pending("c10", "C1"): {"thread_ts": ["1.0"]}, "/services/connectors/c10/config": {"auth": {}}}
    config = Config({
        "/services/connectors/c1/config": {"auth": {}},
        _pending("c1", "C1"): {"thread_ts": ["1.0"]},
        _pending("c1", "C2"): {"thread_ts": ["2.0"]},
        **other,
    })
    assert await delete_connector_credentials(MagicMock(), config, "c1") is True
    # A connector whose id only starts with the deleted one's keeps its keys.
    assert config.kv == other


async def test_a_pending_thread_key_the_store_keeps_is_reported() -> None:
    kept = _pending("c1", "C1")
    config = Config({"/services/connectors/c1/config": {"auth": {}}, kept: {"thread_ts": ["1.0"]}}, frozenset({kept}))
    # Not done: the storage release keeps its intent and tries again.
    assert await delete_connector_credentials(MagicMock(), config, "c1") is False


def test_both_slack_connectors_keep_pending_threads_under_the_connector_directory() -> None:
    owner = SimpleNamespace(connector_id="c1")
    for connector in (SlackConnector, SlackIndividualConnector):
        assert connector._pending_threads_path(owner, "C1").startswith("/services/connectors/c1/")


async def test_a_kept_credential_still_lets_the_pending_threads_go() -> None:
    config_key = "/services/connectors/c1/config"
    config = Config(
        {config_key: {"auth": {}}, _pending("c1", "C1"): {"thread_ts": ["1.0"]}}, frozenset({config_key}),
    )
    assert await delete_connector_credentials(MagicMock(), config, "c1") is False
    # The retry finds only what is left, not the pending threads too.
    assert config.kv == {config_key: {"auth": {}}}
