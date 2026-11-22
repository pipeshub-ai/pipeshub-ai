import logging

import pytest

from app.core.db import config
from app.core.db.config import DbConfig, warn_if_plaintext_remote

SECRET = "s3cretpw"


@pytest.fixture(autouse=True)
def _fresh_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_PLAINTEXT_WARNED", set())


@pytest.mark.parametrize("query", ["", "?sslmode=disable", "?sslmode=prefer", "?sslmode=allow"])
def test_remote_without_tls_warns_without_the_url(caplog: pytest.LogCaptureFixture, query: str) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_plaintext_remote("PG_CORE_URL", f"postgresql://app:{SECRET}@db.example.com:5432/x{query}")
    assert len(caplog.records) == 1
    assert "PG_CORE_URL" in caplog.text
    assert SECRET not in caplog.text and "db.example.com" not in caplog.text


@pytest.mark.parametrize("host", ["pgbouncer", "10.0.0.5", "db.example.com"])
def test_implicit_prefer_on_any_remote_host_warns(caplog: pytest.LogCaptureFixture, host: str) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_plaintext_remote("PG_CORE_URL", f"postgresql://app:{SECRET}@{host}:5432/x")
    assert len(caplog.records) == 1


@pytest.mark.parametrize("url", [
    f"postgresql://app:{SECRET}@localhost/x",
    f"postgresql://app:{SECRET}@127.0.0.1/x",
    f"postgresql://app:{SECRET}@[::1]/x",
    f"postgresql://app:{SECRET}@/x?host=/var/run/postgresql",
    f"postgresql://app:{SECRET}@db.example.com/x?sslmode=verify-full",
    f"postgresql+psycopg://app:{SECRET}@db.example.com/x?sslmode=require",
    "",
])
def test_local_or_tls_is_silent(caplog: pytest.LogCaptureFixture, url: str) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_plaintext_remote("PG_CORE_URL", url)
    assert not caplog.records


def test_same_variable_and_url_warns_once_per_process(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PG_CORE_URL", f"postgresql://app:{SECRET}@pgbouncer:6432/x")
    monkeypatch.setenv("PG_JOBS_URL", f"postgresql://app:{SECRET}@pgbouncer:6432/jobs")
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            DbConfig.from_env("query")
    assert sorted(r.getMessage().split()[0] for r in caplog.records) == ["PG_CORE_URL", "PG_JOBS_URL"]


def test_another_variable_host_or_sslmode_still_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_plaintext_remote("PG_CORE_URL", "postgresql://app@pgbouncer/x")
        warn_if_plaintext_remote("PG_CORE_URL", "postgresql://app@pgbouncer/x")
        warn_if_plaintext_remote("PGVECTOR_DSN", "postgresql://app@pgbouncer/x")
        warn_if_plaintext_remote("PG_CORE_URL", "postgresql://app@db.example.com/x")
        warn_if_plaintext_remote("PG_CORE_URL", "postgresql://app@pgbouncer/x?sslmode=disable")
    assert len(caplog.records) == 4
