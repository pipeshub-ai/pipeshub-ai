import pytest

from app.utils.store_umbrella import POSTGRES_SETTINGS, apply_store_umbrella


def test_unset_changes_nothing():
    env: dict[str, str] = {}
    assert apply_store_umbrella(env) is False
    assert env == {}


def test_postgres_fills_every_unset_store_setting():
    env = {"PIPESHUB_STORE": "Postgres"}
    assert apply_store_umbrella(env) is True
    assert {k: env[k] for k in POSTGRES_SETTINGS} == POSTGRES_SETTINGS


def test_a_setting_that_is_set_wins():
    env = {"PIPESHUB_STORE": "postgres", "MESSAGE_BROKER": "kafka", "REDIS_MODE": "standalone"}
    apply_store_umbrella(env)
    assert env["MESSAGE_BROKER"] == "kafka"
    assert env["REDIS_MODE"] == "standalone"
    assert env["KV_STORE_TYPE"] == "postgres"


def test_unknown_value_is_refused():
    with pytest.raises(ValueError):
        apply_store_umbrella({"PIPESHUB_STORE": "sqlite"})


def test_dotenv_setting_wins_and_umbrella_can_come_from_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PIPESHUB_STORE=postgres\nKV_STORE_TYPE=etcd\n")
    monkeypatch.chdir(tmp_path)
    for key in ("PIPESHUB_STORE", *POSTGRES_SETTINGS):
        monkeypatch.delenv(key, raising=False)
    assert apply_store_umbrella() is True
    import os

    assert "KV_STORE_TYPE" not in os.environ  # left to .env
    assert os.environ["MESSAGE_BROKER"] == "postgres"
    for key in POSTGRES_SETTINGS:  # monkeypatch restores only what it saw; clean what we set
        monkeypatch.delenv(key, raising=False)
