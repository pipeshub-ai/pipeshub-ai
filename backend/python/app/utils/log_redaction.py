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

from app.utils.sensitive_names import is_credential_name, name_words
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
_NO_VALUE_WORDS = frozenset({"true", "false", "none", "null", "undefined"})
_NOT_SECRET_VALUES = _NO_VALUE_WORDS | {"changeme", "password"}

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
# A key may follow `_`, `-` or `.` (as in `report_sk-....txt`) but not a letter
# or digit, so `task-...` and `disk-...` stay. A JWT keeps the stricter rule: a
# run like `_eyJ_eyJ...` would otherwise be rescanned from every `eyJ`.
_KEY_SHAPES = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    rf"phpat_[{_TOKEN}]{{8,}}"
    rf"|sk-[{_TOKEN}]{{20,}}"
    rf"|(?:AKIA|ASIA)[0-9A-Z]{{16}}(?![0-9A-Za-z])"
    rf"|xox[abposr]-[A-Za-z0-9\-]{{10,}}"
    rf"|AIza[{_TOKEN}]{{30,}}"
    rf"|gh[pousr]_[A-Za-z0-9]{{36,}}"
    rf"|github_pat_[A-Za-z0-9_]{{22,}}"
    rf"|(?<![_\-])eyJ[{_TOKEN}]{{8,}}\.[{_TOKEN}]{{8,}}\.[{_TOKEN}]*"
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
# Here and in _PYDANTIC_INPUT_VALUE, runs of plain characters are one
# character-class repeat: re keeps backtracking state for every pass through
# a repeated group (about 120 bytes per character of a 1 MB quoted value).
_PAIR_VALUE = re.compile(
    r"b?\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"?"
    r"|b?'[^'\\\n]*(?:\\.[^'\\\n]*)*'?"
    r"|(?:(?i:bearer|basic|token)[ \t]+)?[^\s,;&'\"(){}\[\]<>]+"
)
# str(pydantic.ValidationError) echoes each rejected value as
# `input_value=<repr>, input_type=<type>`; the repr can be a password. A repr
# may hold `]` or `,` itself, so the value runs to `, input_type=` or, when
# that was cut off, to the end of the line.
_PYDANTIC_INPUT_VALUE = re.compile(r"(input_value=)(?!, input_type=)[^\n][^,\n]*(?:,(?! input_type=)[^,\n]*)*")

# A traceback's source line: the line after `File "...", line N, in f`, two
# spaces deeper under the same `|` gutter (exception groups). It is code read
# from a source file, where `token = await f()` is code, not a pair. `, in` ends the
# digit run, and leaves out a SyntaxError's frame, whose line is the parsed input.
_TRACEBACK_SOURCE_LINE = re.compile(r'^([ |]*)File "[^"\n]*", line \d+, in [^\n]*\n\1  (\S[^\n]*)', re.MULTILINE)

_PAIR_TRIGGERS = ("pass", "pwd", "secret", "token", "sig", "credential", "api", "auth", "cookie", "private", "key", "connection")
_AUTH_SCHEME_WORDS = frozenset({"bearer", "basic", "token"})
# Counts, ports and limits next to a credential word (`token_limit=4096`).
_SHORT_NUMBER = re.compile(r"\d{1,6}")
# Bare pair values that say whether a credential is there, not what it is (`refresh_token=None`, `access_token=present`).
_PRESENCE_WORDS = _NO_VALUE_WORDS | {"present", "missing"}
# The name holds a time when no credential word follows its last time word: token_expires_at, not updated_password.
_TIME_NAME_WORDS = frozenset({
    "expires", "expire", "expiry", "expiration", "exp", "ttl", "timeout", "issued", "created", "updated", "refreshed",
})
_TIME_VALUE = re.compile(r"\d[\d.:+\-TZ]*")
# OAuth endpoints, public by design. Other *_url names stay masked: a reset link or a webhook holds its secret in the
# path. authorize_url, revocation_endpoint, jwks_uri and the like are not credential names, so they are never masked.
_OAUTH_ENDPOINT_NAMES = frozenset({"token_url", "token_uri", "token_endpoint", "authorization_url", "authorization_endpoint"})
# Scheme, host and path only: no userinfo, query, fragment, pair or escape that could carry a secret.
_PUBLIC_URL = re.compile(r"(?i:https?)://[^\s'\"\\?#@=;&%]+")
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
    if 'File "' not in text:
        return _redact_pairs_in(text)
    out = []
    pos = 0
    for source in _TRACEBACK_SOURCE_LINE.finditer(text):
        out += (_redact_pairs_in(text[pos : source.start(2)]), _redact_pairs_in(source.group(2), literals_only=True))
        pos = source.end(2)
    out.append(_redact_pairs_in(text[pos:]))
    return "".join(out)


def _is_time(words: list[str], value: str) -> bool:
    if not _TIME_VALUE.fullmatch(value):
        return False
    last = max((i for i, w in enumerate(words) if w in _TIME_NAME_WORDS), default=-1)
    return last >= 0 and not is_credential_name("_".join(words[last + 1 :]))


def _is_oauth_endpoint(words: list[str], value: str) -> bool:
    return "_".join(words) in _OAUTH_ENDPOINT_NAMES and _PUBLIC_URL.fullmatch(value) is not None


def _redact_pairs_in(text: str, *, literals_only: bool = False) -> str:
    """``literals_only`` is for a traceback's source line, which is code: a
    pair there is masked only when its value is a non-empty ``"..."``,
    ``'...'`` or ``b'...'`` literal (a hard-coded secret), since any other
    value is an expression. A pair inside a string or after a ``#`` is text and
    keeps the normal rule. Other literals (``r``/``f`` prefixes, triple quotes)
    leave their content unmasked, as on main."""
    out = []
    pos = 0
    scanned = 0
    double = single = 0
    comment = False
    url_end = 0
    for head in _PAIR_HEAD.finditer(text):
        if head.start() < pos or not is_credential_name(head.group(1)):
            continue
        in_code = False
        if literals_only:
            # A pair inside a string or comment is text, not code: `"password=x"`.
            double += text.count('"', scanned, head.end())
            single += text.count("'", scanned, head.end())
            comment = comment or "#" in text[scanned : head.end()]
            scanned = head.end()
            in_code = not (double % 2 or single % 2 or comment)
        # Before the value match: a skipped bare value does not move `pos`, so matching it would rescan the run.
        if in_code and not _QUOTED.match(text, head.end()):
            continue
        value = _PAIR_VALUE.match(text, head.end())
        if value is None:
            continue
        raw = value.group()
        lowered = raw.lower()
        if lowered in _AUTH_SCHEME_WORDS or lowered in _PRESENCE_WORDS or _SHORT_NUMBER.fullmatch(raw):
            continue
        opening = _QUOTED.match(raw)
        if in_code and len(raw) <= len(opening.group()) + 1:
            continue
        prefix = opening.group() if opening else ""
        closing = opening.group(2) if opening and len(raw) > len(prefix) and raw.endswith(opening.group(2)) else ""
        content = raw[len(prefix) : len(raw) - len(closing)]
        words = name_words(head.group(1))
        if _is_time(words, content):
            continue
        # Heads inside a kept URL are not skipped, so one there that kept the rest of the run too would make this quadratic.
        if head.start() >= url_end and _is_oauth_endpoint(words, content):
            url_end = value.end()
            continue
        out.append(f"{text[pos : head.end()]}{prefix}{REDACTED}{closing}")
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
    ``Bearer``/``Basic`` credentials, and URL passwords and query secrets.

    A pair value that only describes the credential is kept: a bare presence
    word (``None``, ``true``, ``missing``, ...), a time under a name whose
    last time word no credential word follows (``token_expires_at=1730000000``),
    and an ``http(s)`` scheme, host and path under an OAuth endpoint name
    (``token_url``, ``tokenUri``, ``authorization_endpoint``, ...; a dotted
    ``oauth.tokenUrl`` stays masked).

    On a traceback's source lines a pair in code is masked only when its value
    is a string literal, so ``token = await f()`` stays readable. A bare value
    is kept there: a long number (``password = 12345678``), a non-string local
    that ``capture_locals`` prints under a frame with no source, a one-character
    unterminated literal, a pair after an odd number of escaped quotes inside
    a literal (``'it\\'s token=v'``), and anything under a fake frame line."""
    if not text:
        return text
    lower = text.lower()
    if "PRIVATE KEY" in text:
        text = _redact_private_keys(text)
    for secret in _env_secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    if "input_value=" in text:
        text = _PYDANTIC_INPUT_VALUE.sub(rf"\g<1>{REDACTED}", text)
    if any(t in lower for t in _PAIR_TRIGGERS):
        text = _redact_pairs(text)
    # After pairs: a `[REDACTED]` inserted inside a pair value would cut that value short.
    if any(t in lower for t in _KEY_SHAPE_TRIGGERS):
        text = _KEY_SHAPES.sub(REDACTED, text)
    if "bearer" in lower or "Basic" in text:
        text = _AUTH_SCHEME.sub(lambda m: f"{m.group(1) or m.group(2)} {REDACTED}", text)
    if "://" in text:
        text = _redact_urls(text)
    return text



URL_PATTERN = re.compile(r"https?://\S+")
# OpenAI-style organization/project/account ids (org-abc123, proj_x9...). A digit is required and
# `.py`/`(` must not follow, so our own names (org_context.py, org_id_hash, account_service.get) survive.
# The digit is checked in `_mask_provider_account_id`: a lookahead here would rescan the rest of the
# token at every `org-` in it, which is quadratic.
PROVIDER_ACCOUNT_ID_PATTERN = re.compile(r"\b(?:org|proj|acct|account)[-_][A-Za-z0-9_-]{4,}\b(?!\.py|\()")
_DIGIT_AHEAD = re.compile(r"[A-Za-z0-9_-]*\d")


def _mask_provider_account_id(match: re.Match[str]) -> str:
    # From the match start, as the prefix has no digit; the digit may sit past where `(?!\.py|\()` ended the match.
    return "[redacted]" if _DIGIT_AHEAD.match(match.string, match.start()) else match.group()


def redact_error_for_log(error_msg: str, max_len: int = 1000) -> str:
    """The raw error with URLs, account ids and credentials masked, capped at `max_len`.

    A provider's error can echo the request URL, whose query string may carry
    a credential (Google's REST endpoints accept `?key=`), or a request header.
    """
    text = URL_PATTERN.sub("[url]", error_msg)
    text = PROVIDER_ACCOUNT_ID_PATTERN.sub(_mask_provider_account_id, text)
    return redact_secrets_in_text(text)[:max_len]

# `input` is the rejected value itself and `ctx` can quote it back.
_UNSAFE_VALIDATION_ERROR_KEYS = frozenset({"input", "ctx"})


def log_safe_validation_errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pydantic or FastAPI validation errors reduced to what a log may hold:
    each error's ``loc``, ``type`` and ``msg``, never the rejected input."""
    return [{k: v for k, v in error.items() if k not in _UNSAFE_VALIDATION_ERROR_KEYS} for error in errors]
