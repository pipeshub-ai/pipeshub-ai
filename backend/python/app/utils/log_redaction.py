"""Mask credentials in free text on its way to a log.

The Python counterpart of ``redactSecretsInText`` in
backend/nodejs/apps/src/libs/utils/log-redaction.utils.ts, applied to every
formatted log line by ``RedactingFormatter`` (app/utils/logger.py). It is a
safety net under the rule that call sites never log a stored value, not a
replacement for it.

Every pattern runs in time linear in the text: each run of token characters
can start a match at only one position (a look-behind on the same character
class), no quantified group nests another, and the URL redactor, which is not
linear, only sees URLs up to a bounded size. A pass whose trigger substring is
absent is skipped, so an ordinary line costs a few substring checks.
"""

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.utils.sensitive_names import is_credential_name
from app.utils.url_redaction import redact_sensitive_query_params, redact_url

REDACTED = "[REDACTED]"
_REDACTED_KEY = "[REDACTED PRIVATE KEY]"

# Env vars whose names carry one of these words hold this process's own
# credentials (SECRET_KEY, REDIS_PASSWORD, QDRANT_API_KEY, ...).
_ENV_SECRET_WORDS = frozenset({"PASSWORD", "PASSWD", "SECRET", "TOKEN", "APIKEY", "CREDENTIALS"})
_ENV_KEY_QUALIFIERS = frozenset({"API", "PRIVATE", "SECRET", "ACCESS", "ENCRYPTION", "SIGNING"})
# ...unless the name says the value is about the secret, not the secret.
_ENV_NOT_SECRET_WORDS = frozenset({
    "URL", "URI", "PATH", "FILE", "DIR", "ID", "NAME", "TYPE", "HEADER", "PREFIX", "NAMESPACE",
    "EXPIRY", "EXPIRES", "TTL", "TIMEOUT", "LIMIT", "MAX", "MIN", "COUNT", "SIZE", "ENABLED", "CONFIG",
})
# Shorter values would mask ordinary words and numbers all over the logs.
_MIN_ENV_SECRET_LENGTH = 8
_NOT_SECRET_VALUES = frozenset({"true", "false", "none", "null", "undefined", "changeme", "password"})

_env_secrets: list[str] = []


def _is_secret_env_name(name: str) -> bool:
    words = name.upper().split("_")
    if any(w in _ENV_NOT_SECRET_WORDS for w in words):
        return False
    return any(
        w in _ENV_SECRET_WORDS or (w == "KEY" and i and words[i - 1] in _ENV_KEY_QUALIFIERS)
        for i, w in enumerate(words)
    )


def refresh_env_secrets() -> None:
    """Re-read which env values to mask. Called at import and again once a
    service has loaded its environment (``install_log_redaction``)."""
    values = {
        value
        for name, value in os.environ.items()
        if len(value) >= _MIN_ENV_SECRET_LENGTH
        and value.lower() not in _NOT_SECRET_VALUES
        and not value.isdigit()
        and _is_secret_env_name(name)
    }
    # Longest first, so a secret that contains another is masked whole.
    _env_secrets[:] = sorted(values, key=len, reverse=True)


refresh_env_secrets()


_TOKEN = r"A-Za-z0-9_\-"
_KEY_SHAPES = re.compile(
    rf"(?<![{_TOKEN}])(?:"
    rf"phpat_[{_TOKEN}]{{8,}}"
    rf"|sk-[{_TOKEN}]{{20,}}"
    rf"|(?:AKIA|ASIA)[0-9A-Z]{{16}}(?![0-9A-Za-z])"
    rf"|xox[abposr]-[A-Za-z0-9\-]{{10,}}"
    rf"|AIza[{_TOKEN}]{{30,}}"
    rf"|gh[pousr]_[A-Za-z0-9]{{36,}}"
    rf"|github_pat_[A-Za-z0-9_]{{22,}}"
    rf"|eyJ[{_TOKEN}]{{8,}}\.[{_TOKEN}]{{8,}}\.[{_TOKEN}]*"
    rf")"
)
_KEY_SHAPE_TRIGGERS = ("phpat_", "sk-", "akia", "asia", "xox", "aiza", "gh", "eyj")

# `Bearer` is matched in any case; lowercase "basic" is an ordinary word, so
# only `Basic` followed by something base64-shaped is taken as a credential.
_AUTH_SCHEME = re.compile(
    r"\b((?i:bearer))[ \t]+(?!\[REDACTED)[^\s'\",;]+"
    r"|\b(Basic)[ \t]+[A-Za-z0-9+/]{6,}={0,2}(?![^\s'\",;])"
)

# `name: value`, `name=value`, `"name": "value"` and `'name': 'value'`. Only
# the name and separator are matched here; the value is matched separately,
# and only after the name has been found to be a credential, so a value
# that holds a further pair (`Error: password=x`) is still scanned.
_PAIR_HEAD = re.compile(r"(?<![A-Za-z0-9_.\-])([A-Za-z][A-Za-z0-9_.\-]{0,127})['\"]?[ \t]*[:=][ \t]*")
_PAIR_VALUE = re.compile(
    r"b?\"(?:[^\"\\\n]|\\.)*\"?"
    r"|b?'(?:[^'\\\n]|\\.)*'?"
    r"|(?:(?i:bearer|basic|token)[ \t]+)?[^\s,;&'\"(){}\[\]<>]+"
)
# str(pydantic.ValidationError) echoes each rejected value as
# `input_value=<repr>, input_type=<type>`; the repr can be a password. A repr
# may hold `]` or `,` itself, so the value runs to `, input_type=` or, when
# that was cut off, to the end of the line.
_PYDANTIC_INPUT_VALUE = re.compile(r"(input_value=)(?:(?!, input_type=)[^\n])+")

_PAIR_TRIGGERS = ("pass", "pwd", "secret", "token", "sig", "credential", "api", "auth", "cookie", "private", "key", "connection")
_AUTH_SCHEME_WORDS = frozenset({"bearer", "basic", "token"})
# Counts, ports and limits next to a credential word (`token_limit=4096`).
_SHORT_NUMBER = re.compile(r"\d{1,6}")
_QUOTED = re.compile(r"(b?)(['\"])")

# The URL redactor is not linear in the length of a URL, so a longer one, or
# any past this much URL text in one line, loses its whole query instead.
_MAX_URL_LENGTH = 1024
_URL_BUDGET = 4096
_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-")
_URL_TRAILER = "'\",;)]}>"
_MAX_SCHEME_LENGTH = 32


def _redact_private_keys(text: str) -> str:
    out = []
    pos = 0
    while True:
        begin = text.find("-----BEGIN ", pos)
        if begin < 0:
            break
        header_end = text.find("-----", begin + 11, begin + 11 + 64)
        if header_end < 0 or not text[begin:header_end].endswith("PRIVATE KEY"):
            out.append(text[pos : begin + 11])
            pos = begin + 11
            continue
        out.append(text[pos:begin])
        out.append(_REDACTED_KEY)
        end = text.find("-----END ", header_end + 5)
        if end < 0:
            # A key cut off by truncation is still a key.
            pos = len(text)
            break
        close = text.find("-----", end + 9, end + 9 + 64)
        pos = close + 5 if close >= 0 else end + 9
    out.append(text[pos:])
    return "".join(out)


def _redact_pairs(text: str) -> str:
    out = []
    pos = 0
    for head in _PAIR_HEAD.finditer(text):
        if head.start() < pos or not is_credential_name(head.group(1)):
            continue
        value = _PAIR_VALUE.match(text, head.end())
        if value is None:
            continue
        raw = value.group()
        if raw.lower() in _AUTH_SCHEME_WORDS or _SHORT_NUMBER.fullmatch(raw):
            continue
        opening = _QUOTED.match(raw)
        out.append(text[pos : head.end()])
        if opening:
            closing = opening.group(2) if len(raw) > len(opening.group()) and raw.endswith(opening.group(2)) else ""
            out.append(f"{opening.group()}{REDACTED}{closing}")
        else:
            out.append(REDACTED)
        pos = value.end()
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def _redact_urls(text: str) -> str:
    budget = _URL_BUDGET
    words = re.split(r"(\s+)", text)
    changed = False
    for i, word in enumerate(words):
        sep = word.find("://")
        if sep < 0:
            continue
        start = sep
        while start > sep - _MAX_SCHEME_LENGTH and start and word[start - 1] in _SCHEME_CHARS:
            start -= 1
        end = len(word.rstrip(_URL_TRAILER))
        if end <= sep:
            continue
        url = word[start:end]
        if len(url) <= _MAX_URL_LENGTH and len(url) <= budget:
            budget -= len(url)
            redacted = redact_sensitive_query_params(url)
        else:
            redacted = redact_url(url)
        if redacted != url:
            words[i] = f"{word[:start]}{redacted}{word[end:]}"
            changed = True
    return "".join(words) if changed else text


def redact_secrets_in_text(text: str) -> str:
    """``text`` with credentials masked: private keys, this process's secret
    env values, known key shapes (``phpat_``, ``sk-``, ``AKIA``, ``xox*-``,
    ``AIza``, GitHub tokens, JWTs), credential-named ``name: value`` pairs,
    ``Bearer``/``Basic`` credentials, and URL passwords and query secrets."""
    if not text:
        return text
    lower = text.lower()
    if "PRIVATE KEY" in text:
        text = _redact_private_keys(text)
    for secret in _env_secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    if any(t in lower for t in _KEY_SHAPE_TRIGGERS):
        text = _KEY_SHAPES.sub(REDACTED, text)
    if "input_value=" in text:
        text = _PYDANTIC_INPUT_VALUE.sub(rf"\g<1>{REDACTED}", text)
    if any(t in lower for t in _PAIR_TRIGGERS):
        text = _redact_pairs(text)
    if "bearer" in lower or "Basic" in text:
        text = _AUTH_SCHEME.sub(lambda m: f"{m.group(1) or m.group(2)} {REDACTED}", text)
    if "://" in text:
        text = _redact_urls(text)
    return text


# `input` is the rejected value itself and `ctx` can quote it back.
_UNSAFE_VALIDATION_ERROR_KEYS = frozenset({"input", "ctx"})


def log_safe_validation_errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pydantic or FastAPI validation errors reduced to what a log may hold:
    each error's ``loc``, ``type`` and ``msg``, never the rejected input."""
    return [{k: v for k, v in error.items() if k not in _UNSAFE_VALIDATION_ERROR_KEYS} for error in errors]
