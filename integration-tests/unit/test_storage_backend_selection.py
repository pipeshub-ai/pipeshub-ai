"""Which storage backends a run exercises, and whether it says so.

S3 is parked because the deployment's storage config has no endpoint field, so
only a real AWS bucket can be reached and the stack's MinIO cannot stand in.
That is temporary, and the danger with temporary is that it stops being visible.
"""

from __future__ import annotations

import pytest

from helper.storage_backends import (
    S3_OPT_IN,
    available_backends,
    parked_notice,
    s3_requested,
)

pytestmark = pytest.mark.unit

_CREDENTIALS = ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_REGION", "S3_BUCKET")


def _with_aws_credentials(monkeypatch) -> None:
    for name in _CREDENTIALS:
        monkeypatch.setenv(name, "set")


def test_s3_stays_parked_even_when_the_credentials_are_there(monkeypatch) -> None:
    """CI carries these for the S3 connector tests.

    Before the opt-in existed that alone switched these on, and all 82 failed.
    """
    _with_aws_credentials(monkeypatch)
    monkeypatch.delenv(S3_OPT_IN, raising=False)

    assert available_backends() == ["local"]


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_asking_for_s3_brings_it_back(monkeypatch, value: str) -> None:
    _with_aws_credentials(monkeypatch)
    monkeypatch.setenv(S3_OPT_IN, value)

    assert available_backends() == ["local", "s3"]
    assert s3_requested()


def test_asking_for_s3_without_credentials_does_not_pretend(monkeypatch) -> None:
    for name in _CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(S3_OPT_IN, "1")

    assert available_backends() == ["local"]


def test_a_run_with_s3_parked_says_so(monkeypatch) -> None:
    """A suite that quietly covers less than it did is the failure to avoid."""
    monkeypatch.delenv(S3_OPT_IN, raising=False)

    notice = parked_notice()

    assert notice is not None
    assert S3_OPT_IN in notice
    assert "endpoint" in notice


def test_a_run_that_exercises_s3_says_nothing(monkeypatch) -> None:
    _with_aws_credentials(monkeypatch)
    monkeypatch.setenv(S3_OPT_IN, "1")

    assert parked_notice() is None


def test_asking_for_s3_and_not_getting_it_is_the_loudest_case(monkeypatch) -> None:
    """The silence this module exists to prevent, one level further in.

    An earlier version returned None as soon as S3 was asked for, so a run that
    requested S3 and then dropped it for a missing variable said nothing at all
    — and the test above pinned that silence by setting only the opt-in.
    """
    _with_aws_credentials(monkeypatch)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setenv(S3_OPT_IN, "1")

    notice = parked_notice()

    assert notice is not None
    assert "NOT running" in notice
    assert "S3_BUCKET" in notice
    assert "S3_ACCESS_KEY" not in notice


def test_it_names_every_missing_variable(monkeypatch) -> None:
    for name in _CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(S3_OPT_IN, "1")

    notice = parked_notice()

    assert notice is not None
    for name in _CREDENTIALS:
        assert name in notice
