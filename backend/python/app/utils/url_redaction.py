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


def _logged_name(key: str) -> str:
    """The name a sensitive parameter is logged under: only its leading word
    characters, so whatever a decode left after them
    (``X-Amz-Signature%3D<sig>`` or ``code\\x00``) is never written out with it."""
    match = re.match(r"[\w.-]+", _param_name(key), re.ASCII)
    return match.group() if match else REDACTED


def is_sensitive_query_key(key: str) -> bool:
    """Stray non-alphanumeric bytes around the name (``code%00``, ``code.``,
    ``code ``) do not hide it."""
    name = re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", _param_name(key))
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
# Where a URL that can carry `user:password@` starts: `scheme://`, a
# protocol-relative `//`, or `scheme:` written without the `//`.
_USERINFO_START = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//|^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_USERINFO_COLON = re.compile(r":|%(?:25)*3a", re.IGNORECASE)
# A user name never holds the end of an authority, raw or encoded. It may
# hold an `@` (`alice@example.com:pw@host`) or whitespace (`John Doe:pw@host`).
_NOT_A_USER = re.compile(r"[/?#\[\]]|%(?:25)*(?:2f|3f|23)", re.IGNORECASE)
# `host:8080/…` is a port, not a password: one or more digits, then a raw `/`,
# `?`, `#`, `,` (several hosts), whitespace or the end, and no `@` raw or
# encoded before the query, fragment or end. Past that `?` or `#`, each `&`,
# `;`, `?` or `#` segment that holds an `@` must have an `=` before it: in a
# query an `@` sits inside a pair (`?page=1&email=a@b.com`), while a userinfo
# `@` does not (`app:1234?page=1&secret@host`).
_QUERY_SEGMENT = r"(?:(?!%(?:25)*40)[^@=&;?#])*(?:=[^&;?#]*)?"
_PORT = (
    r"\d+(?=[/?#,\s]|$)(?:(?!%(?:25)*40)[^?#@])*"
    rf"(?:$|[?#]{_QUERY_SEGMENT}(?:[&;?#]{_QUERY_SEGMENT})*$)"
)
_ANY_AT = re.compile(r"@|%(?:25)*40", re.IGNORECASE)
# Query or fragment syntax, raw or encoded, inside what is read as a password.
_QUERY_MARK = re.compile(r"[?#;&]|%(?:25)*(?:3f|23|3b|26)", re.IGNORECASE)


def _split_password_url(url: str) -> tuple[str, str, str] | None:
    """Split a URL that carries a password into (the text before it, the user
    name, what follows it: ``@host…`` or ``?…``), or return None when there is
    none. Kept deliberately simple, since every finer rule has missed an
    encoding; it matches the Node helper:

    - The user name runs to the first ``:`` and holds no ``/``, ``?`` or
      ``#``; it may hold an ``@`` or whitespace.
    - If a port follows that ``:`` (see ``_PORT``), it is not a password.
    - Otherwise everything up to the last ``@`` in the rest of the text is
      the password, whatever it holds (``/``, ``?``, ``#``, spaces, a further
      ``@``, raw or encoded). Nothing is decoded, so the text after that
      ``@`` stays exactly as written.
    - The host after it is kept only when that ``@`` is the only one and no
      query or fragment syntax comes before it. Otherwise either reading
      could put part of a secret there, so the host is replaced too.
    - A ``//`` URL with no ``@`` at all (``https://user:pw?x=1``) has the text
      after the ``:`` replaced up to its query.

    This over-reads ``http://h:abc/x`` or ``https://u:p@h/a@b`` and loses some
    of them to the redaction, which costs readability and never a secret."""
    start = _USERINFO_START.match(url)
    if not start:
        return None
    after = url[start.end():]
    colon = _USERINFO_COLON.search(after)
    if not colon:
        return None
    user = after[: colon.start()]
    if _NOT_A_USER.search(user):
        return None
    rest = after[colon.end():]
    if re.match(_PORT, rest, re.IGNORECASE):
        return None
    ats = list(_ANY_AT.finditer(rest))
    if not ats:
        if not start.group().endswith("//"):
            return None
        query = re.search(r"[?#]|%(?:25)*(?:3f|23)", rest, re.IGNORECASE)
        return start.group(), user, rest[query.start() :] if query else ""
    last = ats[-1]
    settled = len(ats) == 1 and not _QUERY_MARK.search(rest[: last.start()])
    return start.group(), user, "@" + (rest[last.end() :] if settled else REDACTED)


def _redact_userinfo(url: str) -> str:
    """Replace the password of a ``scheme://user:password@host`` URL, for any
    scheme, keeping the user name and host readable as the Node helper does.
    Done on the text, since ``urlparse`` reads a ``/`` in a password as the
    start of the path."""
    parts = _split_password_url(url)
    if parts is None:
        return url
    start, user, rest = parts
    return f"{start}{user}:REDACTED{rest}"


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
            out.append(f"{_logged_name(pair_key)}={REDACTED}")
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
            out.append((_logged_name(key), REDACTED))
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
# The shapes `_split_password_url` reads, anywhere in the text: a user name
# (which may hold an `@` or whitespace) after `//`, or a `scheme:user:` with no `//`, then a
# `:` followed by neither a port nor `REDACTED@`, and an `@` somewhere after
# it, past any spaces. A `user:REDACTED` with an `@` still to come is caught
# too: the redaction stopped short of the real end of the password.
_PASSWORD_URL = re.compile(
    "|".join(
        rf"{head}(?!REDACTED@)(?!{_PORT})[^@]*@"
        for head in (
            r"(?:^|[^a-z0-9+.-])(?:[a-z][a-z0-9+.-]*:)?//[^/?#:\[\]]*:",
            r"(?:^|[^a-z0-9+.-])[a-z][a-z0-9+.-]*:[^/?#:\[\]]+:",
        )
    ),
    re.IGNORECASE,
)
_REDACTED_VALUES = frozenset({"", REDACTED, "REDACTED", "%5BREDACTED%5D", "%5bredacted%5d"})


_ENDS_IN_MARKER = re.compile(r"(?:^|[^a-zA-Z0-9])\[?REDACTED\]?$")
# The same, when the value starts with a separator (`[REDACTED]=?<code>`): a
# marker followed by `=` and anything but an empty value or another marker.
_MARKER_THEN_VALUE = re.compile(r"(?:^|[^a-zA-Z0-9])\[?REDACTED\]?=(?!=*(?:$|[&#;])|\[REDACTED\]|REDACTED\b)")


def _shows_secret_pair(layer: str) -> bool:
    """Every ``name=`` in a segment counts, not just the first: in
    ``returnTo=code=<code>`` or ``code=[REDACTED]=<code>`` the secret follows a
    later ``=``. A value runs to the next ``?``, ``&``, ``#`` or ``;``;
    whitespace does not end it, so ``code =<code>`` is still one pair. A name
    that ends in a redaction marker also counts: the marker may have
    replaced the sensitive name itself (``//u:p@[REDACTED]=<code>``, where
    the host held ``…?code``)."""
    if _MARKER_THEN_VALUE.search(layer):
        return True
    for segment in re.split(r"[?&#;]", layer):
        parts = segment.split("=")
        for k in range(len(parts) - 1):
            name = parts[k]
            # Parsers add a trailing `=` to a bare name, so `[REDACTED]=` is clean.
            value = "=".join(parts[k + 1 :]).rstrip("=")
            sensitive = is_sensitive_query_key(name) or _ENDS_IN_MARKER.search(name)
            if name and sensitive and value not in _REDACTED_VALUES:
                return True
    return False


def _has_residual_secret(text: str) -> bool:
    layer = text
    for _ in range(_MAX_SCAN_LAYERS):
        # Every password shape ends at an `@`, so only the text up to the
        # last one is scanned; that keeps long text with none (or none late)
        # cheap.
        last_at = layer.rfind("@")
        if last_at != -1 and _PASSWORD_URL.search(layer[: last_at + 1]):
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
    parts = _split_password_url(url)
    kept = REDACTED
    if parts:
        # The host is what follows the password, if anything was kept there.
        start, _, rest = parts
        host = re.split(r"[/?#%;&=\s]", rest[1:], maxsplit=1)[0] if rest.startswith("@") else ""
        kept = f"{start}{host}/{REDACTED}"
    elif scheme:
        # The authority after any raw `user@`. It ends at the first encoded
        # character, so an encoded `@` further on is never read as a boundary.
        # The port is left out too: when no password could be split off, what
        # follows the `:` may still be the start of one.
        authority = re.split(r"[/?#%;&=\s]", url[scheme.end():], maxsplit=1)[0]
        host = re.match(r"\[[^\]]*\]|[^:]*", authority.split("@")[-1])
        kept = f"{scheme.group()}{host.group() if host else ''}/{REDACTED}"
    elif url.startswith("/"):
        kept = f"{_KEPT_TEXT_END.split(url, maxsplit=1)[0]}?{REDACTED}"
    return REDACTED if _has_residual_secret(kept) else kept
