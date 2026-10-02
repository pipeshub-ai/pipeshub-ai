from app.utils.env_config import env_int


def test_env_int_unset_returns_default(monkeypatch):
    monkeypatch.delenv("TEST_ENV_INT", raising=False)

    assert env_int("TEST_ENV_INT", 42) == 42


def test_env_int_empty_returns_default(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "")

    assert env_int("TEST_ENV_INT", 42) == 42


def test_env_int_valid_integer_returns_value(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "123")

    assert env_int("TEST_ENV_INT", 42) == 123


def test_env_int_invalid_returns_default(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "abc")

    assert env_int("TEST_ENV_INT", 42) == 42