"""Tests for app.utils.url_redaction."""

import itertools
import re
from collections.abc import Iterator
from urllib.parse import quote, unquote

import pytest
from app.utils.url_redaction import redact_sensitive_query_params, redact_url


class TestRedactUrl:
    def test_strips_userinfo_query_and_fragment(self) -> None:
        raw = "https://user:pass@mcp.example.com:8443/v1/mcp?access_token=secret#frag"
        assert redact_url(raw) == "https://mcp.example.com:8443/v1/mcp"

    def test_preserves_clean_url(self) -> None:
        assert redact_url("https://gitlab.com/api/v4/mcp") == "https://gitlab.com/api/v4/mcp"

    def test_ipv6_host_keeps_brackets_drops_userinfo(self) -> None:
        assert redact_url("https://token@[2001:db8::1]/mcp?key=abc") == "https://[2001:db8::1]/mcp"

    def test_signed_url_loses_its_signature(self) -> None:
        raw = "https://bucket.s3.amazonaws.com/pack.zip?X-Amz-Credential=AKIA&X-Amz-Signature=abc123"
        assert redact_url(raw) == "https://bucket.s3.amazonaws.com/pack.zip"

    @pytest.mark.parametrize("raw", ["not a url", "/relative/path?token=x"])
    def test_non_absolute_urls_are_fully_redacted(self, raw: str) -> None:
        assert redact_url(raw) == "<redacted-url>"

    def test_unparseable_url(self) -> None:
        assert redact_url("https://[::1/x?token=x") == "<unparseable-url>"


class TestRedactSensitiveQueryParams:
    def test_signed_url_token_is_redacted(self) -> None:
        assert (
            redact_sensitive_query_params("/api/v1/index/o/drive/record/r?token=a.b.c&x=1")
            == "/api/v1/index/o/drive/record/r?token=[REDACTED]&x=1"
        )

    def test_param_names_match_case_insensitively(self) -> None:
        assert "secret" not in redact_sensitive_query_params("/cb?Code=secret")

    @pytest.mark.parametrize(
        "url",
        [
            "/api/v1/index/o/drive/record/r?safe=1#token=a.b.c",
            "/api/v1/index/o/drive/record/r#token=a.b.c",
            "/cb?code=a.b.c",
        ],
    )
    def test_raw_hash_segments_are_redacted(self, url: str) -> None:
        """uvicorn's h11 protocol keeps a literal ``#`` in the logged path."""
        assert "a.b.c" not in redact_sensitive_query_params(url)

    @pytest.mark.parametrize(
        "url", ["/api/v1/records", "/search?q=token", "/docs#section", "/a?b=1#c", ""]
    )
    def test_urls_without_sensitive_params_are_unchanged(self, url: str) -> None:
        assert redact_sensitive_query_params(url) == url

    def test_connector_callback_code_and_state_are_redacted(self) -> None:
        out = redact_sensitive_query_params(
            "/api/v1/connectors/oauth/callback?code=4/0FAKE-code&state=ZmFrZS1zdGF0ZQ&scope=drive"
        )
        assert "FAKE-code" not in out
        assert "ZmFrZS1zdGF0ZQ" not in out
        assert "scope=drive" in out

    @pytest.mark.parametrize(
        "key",
        ["key", "apiKey", "X-Api-Key", "client-secret", "sessionToken", "user_password"],
    )
    def test_credential_like_names_are_redacted(self, key: str) -> None:
        assert "hidden-value" not in redact_sensitive_query_params(f"/x?{key}=hidden-value&page=2")

    @pytest.mark.parametrize("key", ["keyword", "monkey", "passage", "statement", "codec"])
    def test_names_that_only_contain_those_letters_are_kept(self, key: str) -> None:
        url = f"/x?{key}=visible-value"
        assert redact_sensitive_query_params(url) == url

    def test_percent_encoded_equals_in_the_name_is_redacted(self) -> None:
        assert "encoded-secret" not in redact_sensitive_query_params("/cb?page=1&code%3Dencoded-secret")

    @pytest.mark.parametrize(
        "url",
        [
            "/cb?code%253DSECRET&page=1",
            "/cb?token%253Dya29.SECRET",
            "/cb?page%3D1%26code%3DSECRET",
            "/cb?foo%26code%3DSECRET",
            "/cb?scope%3Dopenid%26code%3DSECRET%26state%3DSECRET",
            "/login?returnTo%3D%2Fcb%3Fcode%3DSECRET",
        ],
    )
    def test_secrets_hidden_in_an_encoded_name_are_redacted(self, url: str) -> None:
        out = redact_sensitive_query_params(url)
        assert "SECRET" not in out
        assert "SECRET" not in unquote(unquote(out))

    @pytest.mark.parametrize(
        ("url", "secret"),
        [
            ("/cb?page%3D1%26code%3D4%252F0Aea%2526restOfCode123", "restOfCode123"),
            ("/cb?page%3D1%26password%3Ds3cret%2526correct-horse", "correct-horse"),
        ],
    )
    def test_a_secret_holding_an_encoded_ampersand_stays_whole(self, url: str, secret: str) -> None:
        out = redact_sensitive_query_params(url)
        assert secret not in out
        assert secret not in unquote(unquote(out))

    def test_a_nested_code_with_an_encoded_ampersand_stays_whole(self) -> None:
        out = redact_sensitive_query_params("/login?returnTo%3D%2Fcb%3Fcode%3D4%252F0Aea%2526CODEWITHAMP")
        assert "CODEWITHAMP" not in out
        assert "CODEWITHAMP" not in unquote(unquote(out))

    @pytest.mark.parametrize(
        "url",
        [
            "https://user:hunter2%40host/x?page=1",
            "https://user:hunter2%40host/x?code=FAKE",
            "https://user%3Ahunter2@host/x?page=1",
        ],
    )
    def test_a_password_with_an_encoded_delimiter_is_redacted_with_a_query(self, url: str) -> None:
        out = redact_sensitive_query_params(url)
        assert "hunter2" not in out
        assert "FAKE" not in out
        assert "/x?" in out

    @pytest.mark.parametrize(
        ("url", "secret"),
        [
            ("/cb?page%3D1%2526code%3DSECRETTOKEN", "SECRETTOKEN"),
            ("https://user:hunter2%40host%3Fcode%3D4%252F0Aea%2526TAIL", "TAIL"),
            ("/login?returnTo=https%3A%2F%2Fuser%3Ahunter2%2540host%253Fcode%253DSECRET", "SECRET"),
        ],
    )
    def test_a_re_split_separator_does_not_carry_a_secret_out(self, url: str, secret: str) -> None:
        out = redact_sensitive_query_params(url)
        assert secret not in _decode_all(out)
        assert "hunter2" not in _decode_all(out)

    def test_an_encoded_sensitive_name_is_logged_trimmed(self) -> None:
        assert redact_sensitive_query_params("/cb?code%253DSECRET&page=1") == "/cb?code=[REDACTED]&page=1"

    @pytest.mark.parametrize(
        "url",
        [
            "https://user:hunter2@host.example/x?page=2",
            "https://user:hunter2@host.example/x",
            "mongodb://root:hunter2@mongo1:27017,mongo2:27017/db?replicaSet=rs0",
            "https%3A%2F%2Falice%3Ahunter2%40db.example.com",
            "/debug?db=postgres%3A%2F%2Fapp%3Ahunter2%40pg%2Fdb",
        ],
    )
    def test_userinfo_passwords_are_redacted(self, url: str) -> None:
        out = redact_sensitive_query_params(url)
        assert "hunter2" not in out
        assert "hunter2" not in unquote(out)

    def test_userinfo_keeps_user_and_host(self) -> None:
        assert redact_sensitive_query_params("https://user:hunter2@host.example/x?page=2") == (
            "https://user:REDACTED@host.example/x?page=2"
        )

    def test_userinfo_without_a_password_is_unchanged(self) -> None:
        url = "ssh://git@github.com/org/repo"
        assert redact_sensitive_query_params(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "/login?returnTo=%2Fcb%3Fcode%3DSECRET",
            "/login?returnTo=%2Fcb%3Fpage%3D1%26code%3DSECRET&x=1",
            "/login?next=https%253A%252F%252Fapp.example.com%252Fcb%253Fcode%253DSECRET",
            "/login?returnTo=https%3A%2F%2Fapp.example.com%2F%23access_token%3DSECRET",
        ],
    )
    def test_codes_in_nested_url_parameters_are_redacted(self, url: str) -> None:
        out = redact_sensitive_query_params(url)
        assert "SECRET" not in out
        assert "returnTo=" in out or "next=" in out

    def test_nested_url_without_secrets_is_unchanged(self) -> None:
        url = "/login?returnTo=%2Fdocs%3Fpage%3D2"
        assert redact_sensitive_query_params(url) == url

    def test_the_rest_of_an_encoded_authority_stays_encoded(self) -> None:
        """The code is ``4/0Aea&TAILSECRET``: decoding the whole authority at
        once turned its ``%26`` into a separator and logged the tail."""
        out = redact_sensitive_query_params("https://user:hunter2%40host%3Fcode%3D4%2F0Aea%26TAILSECRET")
        assert "TAILSECRET" not in _decode_all(out)
        assert "hunter2" not in _decode_all(out)

    def test_an_at_sign_inside_an_encoded_query_does_not_end_the_userinfo(self) -> None:
        """The ``?`` is encoded as much as the ``@``, so the code's own ``%40``
        lies past the end of the authority and is not where the password stops."""
        out = redact_sensitive_query_params("https://user:hunter2%40host%3Fcode%3D4%2F0Aea%40TAILSECRET")
        assert "TAILSECRET" not in _decode_all(out)
        assert "hunter2" not in _decode_all(out)

    @pytest.mark.parametrize(
        ("url", "password", "expected"),
        [
            ("postgres://app:sec/ret@pg/db", "sec/ret", "postgres://app:REDACTED@pg/db"),
            ("mongodb://root:ab/cd==@mongo:27017/db", "ab/cd", "mongodb://root:REDACTED@mongo:27017/db"),
            ("redis://:p/ass@redis:6379/0", "p/ass", "redis://:REDACTED@redis:6379/0"),
        ],
    )
    def test_a_password_holding_a_slash_is_redacted(self, url: str, password: str, expected: str) -> None:
        assert redact_sensitive_query_params(url) == expected
        head = password.split("/")[0] + "/"
        # The last form sends the whole URL to the fallback, which must not
        # keep the password's head as if it were the host.
        for logged in (f"/debug?db={url}", f"/debug?db={quote(url, safe='')}", f"{url};code=4/0FAKE-code"):
            out = _decode_all(redact_sensitive_query_params(logged))
            assert password not in out, logged
            assert head not in out, logged
            assert "FAKE-code" not in out, logged

    @pytest.mark.parametrize(
        "url",
        [
            "/cb;code=4/0AeaSECRETX",
            "/oauth/callback;code=4/0AeaSECRETX?page=1",
            "/cb/token=SECRETX",
            "/cb&code=SECRETX",
        ],
    )
    def test_a_secret_in_the_path_is_not_written_back(self, url: str) -> None:
        assert "SECRETX" not in _decode_all(redact_sensitive_query_params(url))

    @pytest.mark.parametrize(
        ("url", "secrets"),
        [
            # A `?` or `#` before the `@`, encoded as much as it, is in the password.
            ("https://user:hunter2%3Fextra%40host/db", ["hunter2", "extra"]),
            (quote("https://user:hunter2?extra@host/db", safe=""), ["hunter2", "extra"]),
            ("https://user:hunter2?x=1", ["hunter2"]),
            # A later `@` is counted, and a host after query syntax is not kept.
            ("https://user:p%40SECRETX%3Frest%40host/db", ["SECRETX", "rest"]),
            ("postgres://app:sec%2Fret%3Fmore%40pg/db", ["sec", "more"]),
            ("mongodb://root:ab/cd==%3Fmore%40mongo:27017/db", ["ab/cd", "more"]),
            ("https://user:hunter2%23tail%40host/x?code=SECRETX", ["hunter2", "tail", "SECRETX"]),
            # Protocol-relative, and a scheme with no `//`.
            ("//user:hunter2@host/db", ["hunter2"]),
            ("//user:hunter2%40host/x?page=1", ["hunter2"]),
            ("/debug?db=//user:hunter2@host/db", ["hunter2"]),
            (
                "/login?returnTo=" + quote("//user:hunter2@host/cb?code=FAKECODE", safe=""),
                ["hunter2", "FAKECODE"],
            ),
            ("https:user:hunter2@host/db", ["hunter2"]),
            # A stray byte between a sensitive name and its `=`.
            ("/cb?code%FF%3DSECRETTOKEN", ["SECRETTOKEN"]),
            ("/cb?code%00%3DSECRETTOKEN", ["SECRETTOKEN"]),
            ("/cb?code%2E%3DSECRETTOKEN", ["SECRETTOKEN"]),
            ("/cb?code%20%3DSECRETTOKEN", ["SECRETTOKEN"]),
            # A raw `#` in a nested password ends the outer URL inside it.
            ("/debug?a=1&db=redis://:pass#TAILSECRET@redis:6379/0", ["TAILSECRET"]),
            # A sensitive name that carries its own value after an encoded `=`.
            ("/cb?X-Amz-Signature%253DSECRETSIG%26X-Amz-Date=1", ["SECRETSIG"]),
        ],
    )
    def test_the_secret_is_not_logged(self, url: str, secrets: list[str]) -> None:
        out = _decode_all(redact_sensitive_query_params(url))
        for secret in secrets:
            assert secret not in out, out

    def test_a_port_followed_by_an_at_sign_later_is_still_a_port(self) -> None:
        url = "http://localhost:3001/invite?email=a@b.com"
        assert redact_sensitive_query_params(url) == url

    def test_a_question_mark_inside_a_value_stays_in_it(self) -> None:
        """The query runs from the first ``?`` to the ``#``, as the Node
        helper's URL parser reads it, so ``AB?TAILSECRET`` is all one code."""
        out = redact_sensitive_query_params("/cb?code=AB?TAILSECRET&page=1")
        assert "TAILSECRET" not in _decode_all(out)

    def test_the_fallback_keeps_an_ordinary_path(self) -> None:
        assert redact_sensitive_query_params("/oauth/callback;code=4/0AeaSECRETX?page=1") == "/oauth/callback?[REDACTED]"

    def test_nesting_depth_is_bounded(self) -> None:
        value = "code=SECRET"
        for _ in range(10):
            value = f"/x?next={value.replace('%', '%25').replace('=', '%3D').replace('?', '%3F').replace('&', '%26')}"
        # Must terminate; the innermost layers are past the bound and stay opaque.
        assert isinstance(redact_sensitive_query_params(value), str)


def _decode_all(text: str) -> str:
    for _ in range(12):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


def _encode_char(ch: str, level: int) -> str:
    out = ch
    for i in range(level):
        out = quote(out, safe="") if i == 0 else out.replace("%", "%25")
    return out


# A secret is written as its value would be at the level of the pair around
# it, so `%26` and `%23` are an `&` and a `#` inside the secret. Its two
# halves are checked separately: a parse that splits the secret at one of
# those must still not log the tail.
_SECRETS = ["HEADX", "HEADX%26TAILX", "HEADX%23TAILX", "HEADX%40TAILX", "HEADX/TAILX"]
# A password may also hold a raw `?`, `#` or `@`: each is then encoded exactly
# as much as the separators around it.
_PASSWORD_SECRETS = [*_SECRETS, "HEADX?TAILX", "HEADX#TAILX", "HEADX@TAILX"]
_MARKERS = ["HEADX", "TAILX"]
_QUERY_TEMPLATES = [
    "/cb?code={s}&page=1",
    "/cb?page=1&code={s}",
    "/login?returnTo=/cb?code={s}",
    "https://app.example.com/cb#access_token={s}",
    "/cb;code={s}",
    "/oauth/callback;code={s}?page=1",
    # A stray byte between the name and its `=`.
    "/cb?code%FF={s}&page=1",
    "/cb?code%00={s}",
    "/cb?code.={s}",
    "/cb?code%20={s}",
]
_PASSWORD_TEMPLATES = [
    "https://user:{s}@host.example/x?page=1",
    "https://user:{s}@host.example?code={s}",
    "postgres://app:{s}@pg/db",
    "//user:{s}@host.example/db",
    "https:user:{s}@host.example/db",
]
# Nested in a query, where a raw `#` would end the outer URL first.
_NESTED_PASSWORD_TEMPLATES = [
    "/debug?db=postgres://app:{s}@pg/db",
    "/debug?db=//user:{s}@host/db",
    "/login?returnTo=//user:{s}@host/cb?code={s}",
]
_SEPARATORS = ["=", "&", "?", "#", "@", ":", ";"]


def _variants(template: str) -> Iterator[str]:
    present = [ch for ch in _SEPARATORS if ch in template]
    scheme = re.match(r"^([a-z]+:(?://)?)", template)
    for combo in itertools.product(range(3), repeat=len(present)):
        # The leading scheme's own `:` and `//` stay as they are.
        variant = template.replace(scheme.group(1), "\0", 1) if scheme else template
        for ch, level in zip(present, combo):
            variant = variant.replace(ch, _encode_char(ch, level))
        if scheme:
            variant = variant.replace("\0", scheme.group(1), 1)
        for wrap in range(3):
            yield variant if wrap == 0 else _encode_char(variant, wrap)


def test_the_secret_is_never_readable_at_any_decoding_depth() -> None:
    """Every separator encoded zero, one or two times, and the whole string up
    to twice more: no decoding of the output may show any part of the secret."""
    cases = [
        *(t.format(s=s) for t in _QUERY_TEMPLATES for s in _SECRETS),
        *(t.format(s=s) for t in _PASSWORD_TEMPLATES for s in _PASSWORD_SECRETS),
        *(t.format(s=s) for t in _NESTED_PASSWORD_TEMPLATES for s in _PASSWORD_SECRETS if "#" not in s),
    ]
    checked = 0
    for template in cases:
        for url in _variants(template):
            out = _decode_all(redact_sensitive_query_params(url))
            for marker in _MARKERS:
                assert marker not in out, f"{url} -> {out}"
            checked += 1
    assert checked > 10000
