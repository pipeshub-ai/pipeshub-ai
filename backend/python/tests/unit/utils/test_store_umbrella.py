import os

import pytest

from app.utils.store_umbrella import POSTGRES_SETTINGS, apply_store_umbrella


def test_unset_changes_nothing() -> None:
    env: dict[str, str] = {}
    assert apply_store_umbrella(env) is False
    assert env == {}


def test_postgres_fills_every_unset_store_setting() -> None:
    env = {"PIPESHUB_STORE": "Postgres"}
    assert apply_store_umbrella(env) is True
    assert {k: env[k] for k in POSTGRES_SETTINGS} == POSTGRES_SETTINGS


def test_a_setting_that_is_set_wins() -> None:
    env = {"PIPESHUB_STORE": "postgres", "MESSAGE_BROKER": "kafka", "REDIS_MODE": "standalone"}
    apply_store_umbrella(env)
    assert env["MESSAGE_BROKER"] == "kafka"
    assert env["REDIS_MODE"] == "standalone"
    assert env["KV_STORE_TYPE"] == "postgres"


def test_unknown_value_is_refused() -> None:
    with pytest.raises(ValueError):
        apply_store_umbrella({"PIPESHUB_STORE": "sqlite"})


def test_dotenv_setting_wins_and_umbrella_can_come_from_dotenv(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("PIPESHUB_STORE=postgres\nKV_STORE_TYPE=etcd\n")
    monkeypatch.chdir(tmp_path)
    for key in ("PIPESHUB_STORE", *POSTGRES_SETTINGS):
        monkeypatch.setenv(key, "")  # monkeypatch owns these keys now and puts them back at teardown, whatever happens below
        monkeypatch.delenv(key)
    assert apply_store_umbrella() is True
    assert "KV_STORE_TYPE" not in os.environ  # left to .env
    assert os.environ["MESSAGE_BROKER"] == "postgres"
