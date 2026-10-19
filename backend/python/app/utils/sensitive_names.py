"""Names that mark a value as a credential, in one place for every redactor."""

import re

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

# Further words that mark a `name: value` pair in free log text. Not part of
# CREDENTIAL_WORDS: the URL redactor (and Node) keep those two lists aligned.
LOG_CREDENTIAL_WORDS = frozenset({
    "authorization",
    "cookie",
    "passphrase",
    "privatekey",
    "accesskey",
    "secretkey",
    "connectionstring",
})

# `<qualifier> key` is a credential (`privateKey`, `api_key`); `key` alone is
# not, since KV paths are logged as `key: /services/...`.
_KEY_QUALIFIERS = frozenset({"api", "private", "secret", "access", "auth", "encryption", "signing", "master", "account"})

# OAuth app secret fields, in each spelling the save paths accept.
OAUTH_CLIENT_SECRET_FIELDS = frozenset({"clientSecret", "client_secret", "clientsecret"})

_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_EXCEPTION_SUFFIXES = ("error", "exception", "warning")


def name_words(name: str) -> list[str]:
    """`clientSecret`, `client_secret` and `CLIENT-SECRET` all give ["client", "secret"]."""
    return [w for w in _NON_ALNUM.split(_CAMEL_BOUNDARY.sub(r"\1 \2", name).lower()) if w]


def is_credential_name(name: str) -> bool:
    """Whether a field name in free text (`password`, `apiKey`, `auth.patToken`)
    labels a credential. An exception class name (`InvalidTokenError: ...`)
    labels a message, not a value."""
    words = name_words(name)
    if not words or words[-1] in _EXCEPTION_SUFFIXES:
        return False
    for i, word in enumerate(words):
        if word in CREDENTIAL_WORDS or word in LOG_CREDENTIAL_WORDS:
            return True
        if i and word == "key" and words[i - 1] in _KEY_QUALIFIERS:
            return True
        if i and word == "string" and words[i - 1] == "connection":
            return True
    return False
