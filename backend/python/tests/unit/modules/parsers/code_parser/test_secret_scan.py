"""Credential redaction ahead of parsing, embedding and LLM calls (S-2)."""
from __future__ import annotations

import pytest

from app.modules.parsers.code_parser.file_role import (
    is_sensitive_path,
    should_index_code_file,
)
from app.modules.parsers.code_parser.secret_scan import (
    redact_secrets,
    redact_secrets_bytes,
)

_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA7Yt0aQ\n-----END RSA PRIVATE KEY-----"
# Assembled at runtime so provider secret scanners don't flag this file on push.
_GH = "gh" + "p_0123456789abcdefghijABCDEFGHIJ012345"
_GL = "gl" + "pat-a1B2c3D4e5F6g7H8i9J0"
_STRIPE = "sk" + "_live_4eC39HqLyjWDarjtT1zdp7dc"
_GOOGLE = "AI" + "zaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY"


@pytest.mark.parametrize(
    "text, secret, kind",
    [
        ('AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")',
         "AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
        (f"token = '{_GH}'", _GH, "github-token"),
        (f"GITLAB = '{_GL}'", _GL, "gitlab-token"),
        (f'stripe.api_key = "{_STRIPE}"', _STRIPE, "stripe-key"),
        (f"key: {_GOOGLE}", _GOOGLE, "google-api-key"),
        (f"KEY = '''{_PEM}'''", "MIIEowIBAAKCAQEA7Yt0aQ", "private-key"),
        ("DATABASE_URL=postgresql://acme:s3cr3tPassw0rd@db:5432/acme", "s3cr3tPassw0rd", "url-password"),
        ('PAYMENTS_API_KEY = os.getenv("PAYMENTS_API_KEY", "CANARYSECRET-7f3a9c1e5b2d4086")',
         "CANARYSECRET-7f3a9c1e5b2d4086", "generic-secret"),
        ('export DEPLOY_TOKEN="${DEPLOY_TOKEN:-CANARYSECRET-3f6e9a2d71c08b54}"',
         "CANARYSECRET-3f6e9a2d71c08b54", "generic-secret"),
        ('db_password = "CANARYSECRET-e0d4c2b1a9f87766"', "CANARYSECRET-e0d4c2b1a9f87766", "generic-secret"),
        ('{"client_secret": "9f8e7d6c5b4a39281706f5e4"}', "9f8e7d6c5b4a39281706f5e4", "generic-secret"),
        ("JWT_SIGNING_KEY=k8Hq2Lp9Zr4Wm7Tx", "k8Hq2Lp9Zr4Wm7Tx", "generic-secret"),
    ],
)
def test_secrets_are_redacted(text: str, secret: str, kind: str) -> None:
    result = redact_secrets(text)
    assert secret not in result.text
    assert f"«REDACTED:{kind}»" in result.text
    assert kind in result.kinds


@pytest.mark.parametrize(
    "text",
    [
        'token_url="https://oauth2.googleapis.com/token",',
        'TOKEN_PATH = "/oauth2/v2.0/token"',
        "credentials = base64.b64encode(f\"{user}:{password}\".encode()).decode()",
        "client_via_token = GitHubClientViaToken(token=token,per_page=90)",
        "const resolvedCredentials = resolveS3Credentials({",
        'API_KEY_ENV = "PAYMENTS_API_KEY"',
        'password = os.environ["DB_PASSWORD"]',
        'api_key = "your-api-key-here"',
        'SECRET_KEY = "changeme-in-production-123"',
        "description='postgresql://user:password@host:5432/database'",
        "max_tokens = 4096",
        "def refresh_token(self, token: str) -> str:",
    ],
)
def test_ordinary_code_is_left_alone(text: str) -> None:
    assert redact_secrets(text).count == 0


def test_line_structure_is_preserved() -> None:
    text = 'a = 1\nJWT_SIGNING_KEY = "k8Hq2Lp9Zr4Wm7Tx"\nb = 2\n'
    assert redact_secrets(text).text.splitlines() == ["a = 1", 'JWT_SIGNING_KEY = "«REDACTED:generic-secret»"', "b = 2"]


def test_bytes_without_secrets_come_back_unchanged() -> None:
    raw = b"def f():\n    return 1\n"
    out, result = redact_secrets_bytes(raw)
    assert out is raw
    assert result.count == 0


def test_binary_content_is_not_touched() -> None:
    raw = b"\xff\xfe\x00AKIAIOSFODNN7EXAMPLE"
    out, result = redact_secrets_bytes(raw)
    assert out is raw and result.count == 0


@pytest.mark.parametrize(
    "path, sensitive",
    [
        (".env", True),
        ("services/api/.env.production", True),
        ("config/prod.env", True),
        (".env.example", False),
        ("deploy/certs/server.pem", True),
        ("infra/terraform/prod.tfvars", True),
        ("infra/terraform.tfstate", True),
        ("keys/id_rsa", True),
        ("keys/id_rsa.pub", False),
        ("secrets/client.p12", True),
        (".npmrc", True),
        ("credentials.json", True),
        ("app/credentials_service.py", False),
        ("src/config.env.ts", False),
    ],
)
def test_sensitive_paths(path: str, sensitive: bool) -> None:
    assert is_sensitive_path(path) is sensitive
    if sensitive:
        assert should_index_code_file(path)[0] is False
