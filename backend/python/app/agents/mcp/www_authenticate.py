"""`WWW-Authenticate` challenges (RFC 9110 §11.6.1) and the scopes a Bearer one names (RFC 6750 §3).

A 401's challenge names the metadata and scopes to sign in with (`dcr`); a 403's, with
`error="insufficient_scope"`, the scopes a request needs (`step_up`).
"""
import re

_TOKEN = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_SCHEME_RE = re.compile(rf"\s*({_TOKEN})(?=\s|,|$)")
# Values should be tokens or quoted; servers do send bare URLs, so anything without a space,
# comma or quote is taken.
_PARAM_RE = re.compile(rf'\s*({_TOKEN})\s*=\s*("(?:[^"\\]|\\.)*"|[^\s,"]+)\s*(?:,|$)')
_TOKEN68_RE = re.compile(r"\s*[A-Za-z0-9\-._~+/]+=*\s*(?=,|$)")
_ESCAPED_RE = re.compile(r"\\(.)")
# RFC 6750 §3 scope-token characters.
_SCOPE_TOKEN_RE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")
_MAX_CHALLENGE_CHARS = 8192
MAX_SCOPES = 50
# Longer is junk, not a scope; scopes are stored and shown.
_MAX_SCOPE_CHARS = 200


def _challenges(text: str) -> list[tuple[str, dict[str, str]]]:
    """(scheme, params) for each challenge in one `WWW-Authenticate` value: names lowercased,
    quoted values unescaped, the first of a repeated parameter kept."""
    found: list[tuple[str, dict[str, str]]] = []
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos] in " \t,":
            pos += 1
        scheme = _SCHEME_RE.match(text, pos)
        if scheme is None:
            break
        params: dict[str, str] = {}
        found.append((scheme.group(1).lower(), params))
        pos = scheme.end()
        while (param := _PARAM_RE.match(text, pos)) is not None:
            raw = param.group(2)
            value = _ESCAPED_RE.sub(r"\1", raw[1:-1]) if raw.startswith('"') else raw
            params.setdefault(param.group(1).lower(), value)
            pos = param.end()
        if not params and (token68 := _TOKEN68_RE.match(text, pos)) is not None:
            pos = token68.end()
    return found


def bearer_challenge(values: list[str]) -> dict[str, str]:
    """The parameters of the first Bearer challenge among a response's `WWW-Authenticate`
    values, or {} when there is none."""
    for value in values:
        for scheme, params in _challenges(value[:_MAX_CHALLENGE_CHARS]):
            if scheme == "bearer":
                return params
    return {}


def challenge_scopes(params: dict[str, str]) -> list[str]:
    """A challenge's `scope`: the valid scope tokens, each once, at most `MAX_SCOPES`."""
    scopes = [s for s in params.get("scope", "").split() if len(s) <= _MAX_SCOPE_CHARS and _SCOPE_TOKEN_RE.fullmatch(s)]
    return list(dict.fromkeys(scopes))[:MAX_SCOPES]
