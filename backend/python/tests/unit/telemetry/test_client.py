"""Unit tests for app.telemetry.client."""

import pytest

from app.telemetry.client import classify_request_id


@pytest.mark.parametrize(
    ("request_id", "expected"),
    [
        ("sdk-py-u1-abc", ("sdk", "py")),
        ("sdk-ts-abc", ("sdk", "ts")),
        ("sdk-go-abc", ("sdk", "go")),
        ("sdk-rust-abc", ("sdk", "other")),
        ("mcp-u1-abc", ("mcp", "none")),
        ("mcp-abc-query", ("mcp", "none")),
        ("665f0c2a1b2c3d4e5f607182-V1StGXR8_Z5jdHi6B-myT", ("other", "none")),
        ("anon-abc", ("other", "none")),
        ("sys-abc", ("other", "none")),
        ("sdkfoo", ("other", "none")),
        (None, ("other", "none")),
        ("", ("other", "none")),
    ],
)
def test_classify_request_id(request_id: str | None, expected: tuple[str, str]) -> None:
    assert classify_request_id(request_id) == expected
