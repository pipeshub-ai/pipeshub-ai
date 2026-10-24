"""Redact credentials from repository text before it is parsed, embedded or
sent to an LLM.

Indexed text reaches vector stores, LLM-provider logs and agent transcripts,
and embeddings can be inverted, so a secret has to be gone before any of
that. Rules follow gitleaks' high-precision ones: provider token formats,
private-key blocks, passwords in connection URLs, plus one generic rule for
high-entropy literals assigned to a secret-named variable.

Pure functions -- no I/O.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

__all__ = ["RedactionResult", "redact_secrets", "redact_secrets_bytes"]

# Longer than any code file the parser accepts; past this, skip rather than scan.
_MAX_SCAN_BYTES = 8 * 1024 * 1024

_TOKEN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----",
        re.DOTALL,
    )),
    ("aws-access-key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{80,})\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]+")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{40,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}T3BlbkFJ[A-Za-z0-9_\-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
)

# A literal every match of the rule contains: a file holding none of them is not run
# through that rule's regex. Scanning is per file and on the indexing path, and most
# files hold no secret at all.
_TOKEN_HINTS: dict[str, tuple[str, ...]] = {
    "private-key": ("PRIVATE KEY",),
    "aws-access-key": ("A3T", "AKIA", "ASIA", "ABIA", "ACCA"),
    "github-token": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
    "gitlab-token": ("glpat-",),
    "slack-token": ("xox",),
    "slack-webhook": ("hooks.slack.com",),
    "stripe-key": ("_live_", "_test_"),
    "google-api-key": ("AIza",),
    "anthropic-key": ("sk-ant-",),
    "openai-key": ("T3BlbkFJ",),
    "jwt": ("eyJ",),
}

# scheme://user:PASSWORD@host -- only the password is replaced.
_URL_PASSWORD = re.compile(r"\b([a-z][a-z0-9+.\-]{1,20}://[^\s:/@'\"]+:)([^\s@'\"/]{3,})(@)", re.IGNORECASE)

_SECRET_NAME = re.compile(
    r"(?i)(?:passw(?:or)?d|\bpwd\b|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key"
    r"|signing[_\-]?key|client[_\-]?secret|credential|auth[_\-]?key|encryption[_\-]?key)"
)
# `name = ...`, `name: ...`, `"name": ...`, `name := ...`, `export NAME=...`
# Lowercase literals one of which every _SECRET_NAME match contains, for ASCII text.
_SECRET_NAME_HINTS = ("passw", "pwd", "secret", "token", "api", "access", "private", "signing",
                      "credential", "auth", "encryption")
_ASSIGNMENT = re.compile(r"""^[\s{\[,]*(?:export\s+|(?:const|let|var|final|val)\s+)?["']?([\w.\-]+)["']?\s*(?::=|=|:)(?!=)(.*)$""")
# `=` only as base64 padding: inside a token it is a `key=value` pair.
_VALUE_TOKEN = re.compile(r"[A-Za-z0-9+/_\-.~]{10,}={0,2}")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PLACEHOLDER = re.compile(r"(?i)changeme|example|placeholder|your[_\-]|xxxx|dummy|redacted|<[^>]*>|\$\{|%\(")
# What docs put where a URL's password goes: `user:password@host`.
_URL_PLACEHOLDER = re.compile(r"(?i)^(?:pass(?:word|wd)?|pwd|pw|secret|\*+|x+|\.\.\.)$")


@dataclass(frozen=True)
class RedactionResult:
    text: str
    count: int
    kinds: tuple[str, ...] = ()


def _entropy(value: str) -> float:
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _looks_secret(token: str) -> bool:
    if _ENV_NAME.match(token) or _PLACEHOLDER.search(token):
        return False
    # Paths and URLs (`token_url = "https://.../oauth2/token"`) are endpoints.
    if token.startswith("/") or ("/" in token and "." in token):
        return False
    digits = sum(ch.isdigit() for ch in token)
    if digits < 2 or not re.search(r"[A-Za-z]", token):
        return False
    # Identifiers (`resolveS3Credentials`, `settings.api_key_v2`) are code;
    # an alphanumeric secret carries far more digits.
    if re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*", token) and digits < 4:
        return False
    return _entropy(token) >= 3.0


def _redact_assignment(line: str, kinds: list[str]) -> str:
    body = line.rstrip("\r\n")
    ending = line[len(body):]
    m = _ASSIGNMENT.match(body)
    if not m or not _SECRET_NAME.search(m.group(1)):
        return line
    head, rhs = body[: m.start(2)], m.group(2)

    def sub(tok: re.Match[str]) -> str:
        # `base64.b64encode(...)` is a call, not a value.
        if rhs[tok.end():tok.end() + 1] == "(" or not _looks_secret(tok.group(0)):
            return tok.group(0)
        kinds.append("generic-secret")
        return "«REDACTED:generic-secret»"

    return head + _VALUE_TOKEN.sub(sub, rhs) + ending


def _may_name_a_secret(text: str) -> bool:
    """False only when _SECRET_NAME cannot match. Its IGNORECASE also folds a few
    non-ASCII letters (the long s, the Kelvin sign) that str.lower() keeps, so the
    shortcut is taken for ASCII text only."""
    if not text.isascii():
        return True
    lowered = text.lower()
    return any(hint in lowered for hint in _SECRET_NAME_HINTS)


def redact_secrets(text: str) -> RedactionResult:
    kinds: list[str] = []

    for kind, rule in _TOKEN_RULES:
        if not any(hint in text for hint in _TOKEN_HINTS[kind]):
            continue
        def sub(_m: re.Match[str], kind: str = kind) -> str:
            kinds.append(kind)
            return f"«REDACTED:{kind}»"
        text = rule.sub(sub, text)

    def url_sub(m: re.Match[str]) -> str:
        if _PLACEHOLDER.search(m.group(2)) or _URL_PLACEHOLDER.match(m.group(2)):
            return m.group(0)
        kinds.append("url-password")
        return f"{m.group(1)}«REDACTED:url-password»{m.group(3)}"
    if "://" in text:
        text = _URL_PASSWORD.sub(url_sub, text)

    if _may_name_a_secret(text) and _SECRET_NAME.search(text):
        text = "".join(_redact_assignment(line, kinds) for line in text.splitlines(keepends=True))

    return RedactionResult(text, len(kinds), tuple(sorted(set(kinds))))


def redact_secrets_bytes(content: bytes) -> tuple[bytes, RedactionResult]:
    """Redact text in any ASCII-compatible encoding; binary or oversized content is returned unchanged.

    Bytes that are not UTF-8 (a Latin-1 ``é`` in a comment) round-trip through
    ``surrogateescape`` untouched, so one of them does not exempt the file.
    """
    if len(content) > _MAX_SCAN_BYTES or b"\x00" in content[:8000]:
        return content, RedactionResult("", 0)
    result = redact_secrets(content.decode("utf-8", errors="surrogateescape"))
    if not result.count:
        return content, result
    return result.text.encode("utf-8", errors="surrogateescape"), result
