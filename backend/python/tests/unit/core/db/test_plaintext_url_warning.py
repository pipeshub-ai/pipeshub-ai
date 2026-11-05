import logging

import pytest

from app.core.db.config import warn_if_plaintext_remote

SECRET = "s3cretpw"


@pytest.mark.parametrize("query", ["", "?sslmode=disable", "?sslmode=prefer", "?sslmode=allow"])
def test_remote_without_tls_warns_without_the_url(caplog: pytest.LogCaptureFixture, query: str) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_plaintext_remote("PG_CORE_URL", f"postgresql://app:{SECRET}@db.example.com:5432/x{query}")
    assert len(caplog.records) == 1
    assert "PG_CORE_URL" in caplog.text
    assert SECRET not in caplog.text and "db.example.com" not in caplog.text


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
