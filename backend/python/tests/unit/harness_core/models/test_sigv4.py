"""P4.3 BR-1: SigV4 as AWS computes it (botocore, and AWS's published ``get-vanilla`` vector)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from harness_core.models.sigv4 import AwsCredentials, sign

NOW = datetime(2026, 10, 5, 12, 30, 45, tzinfo=UTC)
CREDS = AwsCredentials("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")


def test_br1_get_vanilla_from_the_aws_test_suite() -> None:
    headers = sign(
        "GET",
        "https://example.amazonaws.com/",
        {},
        b"",
        credentials=CREDS,
        region="us-east-1",
        service="service",
        now=datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC),
    )
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )


@pytest.mark.parametrize("session_token", [None, "FQoGZXIvYXdzEXAMPLETOKEN"])
@pytest.mark.parametrize(
    "path",
    [
        "/model/anthropic.claude-3-5-sonnet-20240620-v1%3A0/converse",
        "/model/us.meta.llama3-2-90b-instruct-v1%3A0/converse-stream",
    ],
)
def test_br1_a_converse_request_is_signed_exactly_as_botocore_signs_it(path: str, session_token: str | None) -> None:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    url = f"https://bedrock-runtime.eu-west-1.amazonaws.com{path}"
    body = json.dumps({"messages": [{"role": "user", "content": [{"text": "hi"}]}]}).encode()
    base = {"content-type": "application/json", "accept": "application/json"}
    creds = AwsCredentials(CREDS.access_key_id, CREDS.secret_access_key, session_token)

    ours = sign("POST", url, base, body, credentials=creds, region="eu-west-1", service="bedrock", now=NOW)

    request = AWSRequest(method="POST", url=url, data=body, headers=dict(base))
    with patch("botocore.auth.get_current_datetime", return_value=NOW.replace(tzinfo=None)):
        SigV4Auth(Credentials(creds.access_key_id, creds.secret_access_key, session_token), "bedrock", "eu-west-1").add_auth(request)
    assert ours["authorization"] == request.headers["Authorization"]
    assert ours["x-amz-date"] == request.headers["X-Amz-Date"]
    assert ours.get("x-amz-security-token") == request.headers.get("X-Amz-Security-Token")


def test_br1_the_secret_never_appears_in_headers_or_repr() -> None:
    headers = sign("POST", "https://h/x", {}, b"{}", credentials=CREDS, region="r", service="bedrock", now=NOW)
    assert all(CREDS.secret_access_key not in v for v in headers.values())
    assert CREDS.secret_access_key not in repr(CREDS)
