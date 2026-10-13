"""Keep credentials in URLs out of logs and error messages."""

import re
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

# Kept in step with backend/nodejs/apps/src/libs/utils/log-redaction.utils.ts.
# `state` is listed because the connector callback accepts exactly the
# (code, state) pair; `key` only ever as a whole parameter name (`?key=`).
SENSITIVE_QUERY_PARAMS = frozenset({
    "code",
    "state",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "api_key",
    "apikey",
    "password",
    "signature",
    "sig",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "se",
    "sp",
})

# Matched against whole words of a name, never substrings, so `passage` or
# `keyword` stay readable while `X-Amz-Signature` or `apiKey` do not.
CREDENTIAL_WORDS = frozenset({
    "password",
    "passwd",
    "pass",
    "pwd",
    "secret",
    "token",
    "signature",
    "sig",
    "credential",
    "credentials",
    "apikey",
})

REDACTED = "[REDACTED]"


# How many layers of URL-inside-a-parameter (or percent-encoding) are opened.
_MAX_NESTING = 3


def _decode_name(key: str) -> str:
    """Parsers split on a raw ``&`` or ``=`` before decoding, so a pair sent as
    ``code%3Dvalue`` arrives as the single name "code=value", and one sent as
    ``code%253Dvalue`` is still ``code%3Dvalue`` after the parser's decode.
    Decoding stops once a raw ``&`` or ``=`` shows, so a ``%26`` inside a
    value stays part of that value when the name is split into pairs."""
    return _decode_layers(key, stop="&=")


def _decode_layers(text: str, stop: str = "") -> str:
    """Percent-decode up to ``_MAX_NESTING`` layers, stopping early once any
    character of ``stop`` shows."""
    for _ in range(_MAX_NESTING):
        if "%" not in text or any(c in text for c in stop):
            break
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


_ESCAPE_RUN = re.compile(r"(?:%[0-9a-fA-F]{2})+")


def _decode_syntax(text: str) -> str:
    """One layer of decoding that never makes a new ``&`` or ``#``. Once a
    text has to be decoded to show its ``?`` and ``=``, an ``%26`` or ``%23``
    at that same layer may sit inside a value (``code%3D4%2F0Aea%26TAIL`` is
    the code ``4/0Aea&TAIL``), so it stays encoded and the parse below reads
    it as part of that value. Run by run, as the Node helper does."""
    return _ESCAPE_RUN.sub(
        lambda run: unquote(run.group()).replace("&", "%26").replace("#", "%23"), text
    )


def _param_name(key: str) -> str:
    return _decode_name(key).split("=", 1)[0]


def is_sensitive_query_key(key: str) -> bool:
    name = _param_name(key)
    if name.lower() in SENSITIVE_QUERY_PARAMS:
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).lower()) if w]
    return any(
        word in CREDENTIAL_WORDS or (word == "api" and words[i + 1 : i + 2] == ["key"])
        for i, word in enumerate(words)
    )


def redact_url(url: str) -> str:
    """Scheme + host[:port] + path only: userinfo, query and fragment can carry tokens."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<unparseable-url>"
    if not parsed.scheme or not parsed.netloc:
        return "<redacted-url>"
    # Drop userinfo (`user:pass@`) while keeping host/port, including bracketed IPv6.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunparse((parsed.scheme, netloc, parsed.path or "", "", "", ""))


# Encoded `=`, `?`, `#`, `&`, `@`, `://`, or a further layer of encoding (`%25`).
_ENCODED_URL_SYNTAX = re.compile(r"%(3d|3f|23|26|40|25)|%3a%2f%2f", re.IGNORECASE)

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# An `@`, `?` or `#`, raw or percent-encoded once or more (`%40`, `%2540`).
_AUTHORITY_MARK = re.compile(r"[@?#]|%((?:25)*)(40|3f|23)", re.IGNORECASE)
_USERINFO_COLON = re.compile(r":|%(?:25)*3a", re.IGNORECASE)


def _split_password_url(url: str) -> tuple[str, str, str] | None:
    """Split ``scheme://user:password@rest`` into (scheme, user, rest), or
    return None when ``url`` has no password. A password may hold ``/``
    (base64 passwords do) and ``@``, so the userinfo runs to the last ``@``
    before the query or fragment; the user name runs to the first ``:`` and
    holds no ``/``. That over-reads ``http://h:8080/a@b`` as a password, which
    costs readability and never a secret.

    A logged URL may carry its ``@``, ``?`` and ``#`` percent-encoded, so each
    is read with its level of encoding (``@`` is 0, ``%40`` is 1, ``%2540`` is
    2). An ``@`` can end the userinfo only if no ``?`` or ``#`` at its level
    or lower comes first: that is what a parser sees once it has decoded just
    far enough to show that ``@``. A value is never encoded less than the
    syntax around it, so the least encoded of those (the last, on a tie) is
    the real end. If the last of them all is a different one, or a ``?`` or
    ``#`` at any level comes before it, the text has been encoded in a way
    that leaves it open: the password then runs to that last ``@``, and the
    host is not kept either, since either reading could put a secret there.
    Nothing is decoded: the text after the ``@`` stays exactly as written, so
    an encoded ``?``, ``&`` or ``#`` there is not turned into syntax here."""
    scheme = _SCHEME.match(url)
    if not scheme:
        return None
    after = url[scheme.end():]
    end_level = float("inf")
    first_end = len(after)
    # (start, end, level) of the last `@` that can end the userinfo, and of
    # the least encoded one.
    last: tuple[int, int, int] | None = None
    least_encoded: tuple[int, int, int] | None = None
    for mark in _AUTHORITY_MARK.finditer(after):
        encoded = mark.group(2)
        level = 0 if encoded is None else 1 + len(mark.group(1)) // 2
        is_at = mark.group() == "@" if encoded is None else encoded == "40"
        if not is_at:
            end_level = min(end_level, level)
            first_end = min(first_end, mark.start())
            if level == 0:
                break
        elif level < end_level:
            last = (mark.start(), mark.end(), level)
            if least_encoded is None or level <= least_encoded[2]:
                least_encoded = last
    if last is None:
        return None
    userinfo = after[: last[0]]
    colon = _USERINFO_COLON.search(userinfo)
    if not colon or "/" in userinfo[: colon.start()]:
        return None
    settled = last == least_encoded and last[0] < first_end
    host = after[last[1] :] if settled else REDACTED
    return scheme.group(), userinfo[: colon.start()], host


def _redact_userinfo(url: str) -> str:
    """Replace the password of a ``scheme://user:password@host`` URL, for any
    scheme, keeping the user name and host readable as the Node helper does.
    Done on the text, since ``urlparse`` reads a ``/`` in a password as the
    start of the path."""
    parts = _split_password_url(url)
    if parts is None:
        return url
    scheme, user, rest = parts
    return f"{scheme}{user}:REDACTED@{rest}"


def _redact_name(key: str, depth: int) -> str:
    """A name that decodes to more than one pair (``page%3D1%26code%3D<code>``
    is the single name "page=1&code=<code>") is logged with those pairs
    redacted. Returns the name unchanged when nothing in it was secret."""
    name = _decode_name(key)
    if depth > _MAX_NESTING or not re.search(r"[=&]", name):
        return key
    # Split on the raw separators only. Each value stays encoded, so a `%26`
    # inside a nested code is still part of that code when the value is
    # itself parsed as a URL or query. Every `&` here was decoded, though, so
    # one may have been inside the secret just redacted: nothing after a
    # redacted pair is kept.
    out = []
    for pair in name.split("&"):
        pair_key, eq, value = pair.partition("=")
        if is_sensitive_query_key(pair_key):
            out.append(f"{_param_name(pair_key)}={REDACTED}")
            return "&".join(out)
        key_out = _redact_name(pair_key, depth + 1)
        value_out = _redact_embedded(value, depth + 1) if eq else value
        if key_out == pair_key and value_out == value:
            out.append(pair)
            continue
        out.append(f"{key_out}={value_out}" if eq else key_out)
        return "&".join(out)
    return key


def _redact_pairs(segment: str, depth: int, *, trusted: bool) -> str:
    """``trusted`` says whether the ``&`` separators of ``segment`` were
    written as raw ``&`` in the logged URL. In text that had to be decoded
    first, an ``&`` may have been an ``%26`` inside the secret just redacted,
    so nothing after a redacted pair is kept."""
    pairs = parse_qsl(segment, keep_blank_values=True)
    changed = False
    out = []
    for key, value in pairs:
        if is_sensitive_query_key(key):
            out.append((_param_name(key), REDACTED))
        else:
            name = _redact_name(key, depth)
            inner = _redact_embedded(value, depth + 1)
            out.append((name, inner))
            if name == key and inner == value:
                continue
        changed = True
        if not trusted:
            break
    return urlencode(out, safe="[]") if changed else segment


def _redact_embedded(value: str, depth: int, *, trusted: bool = False) -> str:
    """A parameter value can itself be a URL or query string (``returnTo``,
    ``redirect_uri``), possibly encoded once more than the outer URL."""
    if depth > _MAX_NESTING or not value:
        return value
    if "?" in value or "#" in value:
        return _redact_segments(value, depth, trusted=trusted)
    # A decoded `scheme://user:password@host` may have no query of its own.
    text = _redact_userinfo(value)
    if text != value and ("?" in text or "#" in text):
        return _redact_segments(text, depth, trusted=trusted)
    if "=" in text:
        return _redact_pairs(text, depth, trusted=trusted)
    if _ENCODED_URL_SYNTAX.search(text):
        # No new `&` or `#` is made, so the separators keep the trust they had.
        decoded = _decode_syntax(text)
        if decoded != text:
            inner = _redact_embedded(decoded, depth + 1, trusted=trusted)
            if inner != decoded:
                return inner
    return text


# Path, query and fragment, split as a URL parser does: the query runs from
# the first `?` to the first `#`, so a `?` inside a value stays in it.
_URL_PARTS = re.compile(r"([^?#]*)(?:\?([^#]*))?(?:#(.*))?", re.DOTALL)


def _redact_segments(url: str, depth: int, *, trusted: bool) -> str:
    url = _redact_userinfo(url)
    match = _URL_PARTS.fullmatch(url)
    path, query, fragment = match.groups() if match else (url, None, None)
    if query is None and fragment is None:
        # A whole URL logged percent-encoded hides its `?` as `%3F`.
        return _redact_embedded(url, depth, trusted=trusted) if depth == 0 else url
    try:
        redacted_query = _redact_pairs(query, depth, trusted=trusted) if query is not None else None
    except ValueError:
        return path
    if fragment is not None:
        if redacted_query != query and not trusted:
            # A decoded `#` may have been inside the secret just redacted.
            fragment = None
        else:
            # uvicorn's h11 protocol keeps a literal `#token=...` in the path.
            fragment = _redact_embedded(fragment, depth + 1, trusted=trusted)
    out = path
    if redacted_query is not None:
        out += f"?{redacted_query}"
    if fragment is not None:
        out += f"#{fragment}"
    return out


def redact_sensitive_query_params(url: str) -> str:
    """Replace the values of credential-bearing query params, keeping the path
    and the rest of the query so access logs stay useful.

    Every segment after a raw ``?`` or ``#`` is treated as query pairs: uvicorn's
    h11 protocol splits the request target only at ``?``, so a literal
    ``#token=...`` sent by a client reaches the access log unparsed. URLs
    nested in parameter values are redacted the same way, a bounded number of
    layers deep, as are the password of a ``user:password@`` URL and pairs
    hidden in a percent-encoded parameter name.
    """
    if not url:
        return url
    # The logged text itself: its raw `&` and `#` are real separators.
    redacted = _redact_segments(url, 0, trusted=True)
    return _redact_wholly(url) if _has_residual_secret(redacted) else redacted


# The safety net below the structural pass. However a URL was encoded, a
# secret only matters if some decoding of the logged text shows it as a
# sensitive `name=value` or a `user:password@` URL. So the output is decoded
# layer by layer, and if any layer still shows one, the text is replaced
# wholesale instead of trusting the parse that missed it.
_MAX_SCAN_LAYERS = 10
# The same shape `_split_password_url` reads: a password may hold `/`.
_PASSWORD_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/?#@:]*:(?!REDACTED@)[^\s?#@]+@", re.IGNORECASE)
_REDACTED_VALUES = frozenset({"", REDACTED, "REDACTED", "%5BREDACTED%5D", "%5bredacted%5d"})


def _shows_secret_pair(layer: str) -> bool:
    """Every ``name=`` in a segment counts, not just the first: in
    ``returnTo=code=<code>`` or ``code=[REDACTED]=<code>`` the secret follows a
    later ``=``. A value runs to the next ``?``, ``&``, ``#`` or ``;``."""
    for segment in re.split(r"[\s?&#;]", layer):
        parts = segment.split("=")
        for k in range(len(parts) - 1):
            name = parts[k]
            # Parsers add a trailing `=` to a bare name, so `[REDACTED]=` is clean.
            value = "=".join(parts[k + 1 :]).rstrip("=")
            if name and is_sensitive_query_key(name) and value not in _REDACTED_VALUES:
                return True
    return False


def _has_residual_secret(text: str) -> bool:
    layer = text
    for _ in range(_MAX_SCAN_LAYERS):
        if _PASSWORD_URL.search(layer):
            return True
        if _shows_secret_pair(layer):
            return True
        decoded = unquote(layer)
        if decoded == layer:
            return False
        layer = decoded
    return False


# Where the kept part of a fallback ends: any query or fragment, an encoded
# character, and every separator the scan above splits pairs on.
_KEPT_TEXT_END = re.compile(r"[?#%;&=\s]")


def _redact_wholly(url: str) -> str:
    """The fallback when the parse missed a secret: keep the scheme and host
    of an absolute URL, or the path of a relative one, and replace the rest.
    If even that kept part shows a secret, nothing is kept."""
    scheme = _SCHEME.match(url)
    kept = REDACTED
    if scheme:
        # After a password, the host is what follows it; otherwise it is the
        # authority after any raw `user@`. It ends at the first encoded
        # character, so an encoded `@` further on is never read as a boundary.
        parts = _split_password_url(url)
        rest = parts[2] if parts else url[scheme.end():]
        authority = re.split(r"[/?#%;&=\s]", rest, maxsplit=1)[0]
        kept = f"{scheme.group()}{authority.split('@')[-1]}/{REDACTED}"
    elif url.startswith("/"):
        kept = f"{_KEPT_TEXT_END.split(url, maxsplit=1)[0]}?{REDACTED}"
    return REDACTED if _has_residual_secret(kept) else kept
