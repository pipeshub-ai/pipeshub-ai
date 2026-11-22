"""The pin that sends S3 virtual-hosted names to a local MinIO in the storage integration tests."""

from __future__ import annotations

import pytest

from tests.integration.vhost_pin import is_pinned_host, pin_getaddrinfo


@pytest.mark.parametrize("host", [
    "bucket.s3.us-east-1.amazonaws.com",
    "BUCKET.S3.US-EAST-1.AMAZONAWS.COM",
    "bucket.s3.us-east-1.amazonaws.com.",
    "Bucket.S3.us-east-1.AmazonAWS.com.",
])
def test_every_spelling_of_an_aws_host_is_pinned(host: str) -> None:
    assert is_pinned_host(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "amazonaws.com.evil.test", "notamazonaws.com", None, b"x.amazonaws.com"])
def test_other_hosts_are_left_alone(host: object) -> None:
    assert not is_pinned_host(host)


def test_pinned_names_resolve_to_the_pin_and_others_to_themselves() -> None:
    seen: list[tuple[object, object]] = []
    pinned = pin_getaddrinfo("127.0.0.1:19143", lambda host, port, *a, **k: seen.append((host, port)))

    pinned("B.S3.US-EAST-1.AMAZONAWS.COM.", 443)
    pinned("example.test", 80)

    assert seen == [("127.0.0.1", 19143), ("example.test", 80)]
