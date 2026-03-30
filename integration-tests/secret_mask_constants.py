"""
Single source of truth for what counts as a secret in integration test logs and reports.

- **Explicit env vars** (`SECRET_ENV_KEYS_FOR_MASKING`): always register their current values.
- **Dynamic env vars** (`ENV_KEY_NAME_SUBSTRINGS_FOR_MASKING`): any ``os.environ`` key whose name
  contains one of these substrings (case-insensitive) and whose value length is at least
  ``SECRET_ENV_VALUE_MIN_LEN`` is treated as a literal secret.
- **Literal masking** uses the same algorithm as pytest-mask-secrets (``re.escape`` + alternation,
  replacement ``*****``). See ``conftest._mask_plaintext_secrets``.
- **Regex passes** (`REGEX_REDACTION_PATTERNS_AFTER_LITERALS`): applied after literal masking for
  shapes that rarely match env bytes-for-byte (PEM blocks, JSON fields, Azure params, headers).

Also honor pytest-mask-secrets: ``MASK_SECRETS`` (comma-separated names), ``MASK_SECRETS_AUTO``
(default on: env keys matching TOKEN|PASSWORD|PASSWD|SECRET), and the plugin stash (populated from
this module in ``pytest_configure``).

When adding a new credential env var for integration tests, add its name to
``SECRET_ENV_KEYS_FOR_MASKING`` if it is fixed and known; otherwise add a substring to
``ENV_KEY_NAME_SUBSTRINGS_FOR_MASKING`` if it follows a naming convention.
"""

from __future__ import annotations

# fmt: off
SECRET_ENV_KEYS_FOR_MASKING: tuple[str, ...] = (
    # Pipeshub API / OAuth
    "CLIENT_ID",
    "CLIENT_SECRET",
    "PIPESHUB_TEST_USER_EMAIL",
    "PIPESHUB_TEST_USER_PASSWORD",
    "PIPESHUB_USER_BEARER_TOKEN",
    # Neo4j
    "TEST_NEO4J_PASSWORD",
    # AWS / S3 (explicit keys also cover boto-style names if set in env for CI)
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    # GCS — JSON key body or ADC
    "GCS_SERVICE_ACCOUNT_JSON",
    "GOOGLE_APPLICATION_CREDENTIALS",
    # Azure Storage
    "AZURE_BLOB_CONNECTION_STRING",
    "AZURE_FILES_CONNECTION_STRING",
)

# Substrings matched against env var *names* (uppercased) for dynamic literal registration.
# Includes pytest-mask-secrets MASK_SECRETS_AUTO-style names (TOKEN, PASSWORD, PASSWD, SECRET)
# plus connector / cloud conventions.
ENV_KEY_NAME_SUBSTRINGS_FOR_MASKING: tuple[str, ...] = (
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "SESSION_TOKEN",
    "CONNECTION_STRING",
    "API_KEY",
    "BEARER",
    "CREDENTIAL",
    "SERVICE_ACCOUNT",
    "AUTH_KEY",
    "SIGNING",
    "CERTIFICATE",
)

SECRET_ENV_VALUE_MIN_LEN: int = 8

# Applied in order after _mask_plaintext_secrets (see conftest._redact_text).
REGEX_REDACTION_PATTERNS_AFTER_LITERALS: tuple[tuple[str, str], ...] = (
    # PEM / PKCS blocks
    (
        r"-----BEGIN [A-Z0-9 -]+-----[\s\S]*?-----END [A-Z0-9 -]+-----",
        "*****",
    ),
    # JSON string values for common secret fields
    (r'("private_key"\s*:\s*")(\\.|[^"\\])*(")', r"\1*****\3"),
    (r'("client_secret"\s*:\s*")(\\.|[^"\\])*(")', r"\1*****\3"),
    (r'("access_token"\s*:\s*")(\\.|[^"\\])*(")', r"\1*****\3"),
    (r'("refresh_token"\s*:\s*")(\\.|[^"\\])*(")', r"\1*****\3"),
    (r'("password"\s*:\s*")(\\.|[^"\\])*(")', r"\1*****\3"),
    # Azure connection string fragments
    (r"(?i)((AccountKey|SharedAccessSignature|Sig)=)([^;\s&]+)", r"\1*****"),
    # HTTP-style auth fragments in log lines
    (r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?bearer\s+)[^'\"\s]+", r"\1*****"),
    (
        r"(?i)((client_secret|access_token|refresh_token|password)\s*['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+",
        r"\1*****",
    ),
)
# fmt: on

__all__ = (
    "SECRET_ENV_KEYS_FOR_MASKING",
    "ENV_KEY_NAME_SUBSTRINGS_FOR_MASKING",
    "SECRET_ENV_VALUE_MIN_LEN",
    "REGEX_REDACTION_PATTERNS_AFTER_LITERALS",
)
