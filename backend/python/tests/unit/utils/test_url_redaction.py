"""Tests for app.utils.url_redaction."""

import itertools
import re
import time
from collections.abc import Iterator
from urllib.parse import quote, unquote

import pytest

from app.utils.url_redaction import (
    REDACTED,
    is_sensitive_query_key,
    redact_sensitive_query_params,
    redact_url,
    redact_urls_in_text,
)


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


class TestRedactUrlsInText:
    def test_strips_query_and_preserves_surrounding_words(self) -> None:
        text = "see https://example.com/a?token=secret now"
        assert redact_urls_in_text(text) == "see https://example.com/a now"

    def test_strips_userinfo_query_and_fragment(self) -> None:
        text = "https://user:pw@host.example/v1?x=1#f done"
        assert redact_urls_in_text(text) == "https://host.example/v1 done"

    def test_redacts_both_urls_and_preserves_words_between_them(self) -> None:
        text = "a https://one.example/p?token=1 b https://two.example/q?sig=2 c"
        assert redact_urls_in_text(text) == (
            "a https://one.example/p b https://two.example/q c"
        )

    def test_preserves_text_without_urls(self) -> None:
        text = "token=secret is not a url"
        assert redact_urls_in_text(text) == text

    def test_preserves_parentheses_and_following_period(self) -> None:
        text = "see (https://example.com/a?token=secret)."
        assert redact_urls_in_text(text) == "see (https://example.com/a)."

    def test_preserves_trailing_period_in_url_path(self) -> None:
        text = "end https://example.com/a."
        assert redact_urls_in_text(text) == text

    def test_preserves_single_quotes_around_url(self) -> None:
        text = "see 'https://example.com/a?token=secret'"
        assert redact_urls_in_text(text) == "see 'https://example.com/a'"

    def test_preserves_empty_text(self) -> None:
        assert redact_urls_in_text("") == ""

    def test_preserves_clean_url_and_surrounding_words(self) -> None:
        text = "clean https://gitlab.com/api/v4/mcp stays"
        assert redact_urls_in_text(text) == text


class TestRedactSensitiveQueryParams:
    def test_signed_url_token_is_redacted(self) -> None:
        assert (
            redact_sensitive_query_params("/api/v1/index/o/drive/record/r?token=a.b.c&x=1")
            == "/api/v1/index/o/drive/record/r?token=[REDACTED]&x=1"
        )

    def test_an_oauth_callbacks_code_and_state_are_replaced(self) -> None:
        out = redact_sensitive_query_params("/api/v1/mcp-servers/oauth/callback?code=c-secret&state=s-secret")
        assert out == f"/api/v1/mcp-servers/oauth/callback?code={REDACTED}&state={REDACTED}"

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
            # A password that starts like a port, or with a separator.
            ("mongodb://root:%2Fab%2Fcd==@mongo:27017/db", ["ab/cd", "ab%2Fcd"]),
            ("https://user:8080%2Fhunter2@host/db", ["hunter2"]),
            ("redis://:%2Fhunter2@redis:6379/0", ["hunter2"]),
            ("postgres://app:%3Fmore%40pg/db", ["more"]),
            ("/debug?db=mongodb://root:%2Fab%2Fcd==@mongo:27017/db", ["ab/cd", "ab%2Fcd"]),
            ("postgres://app:1234/xsecret@pg/db", ["xsecret"]),
            # A port-like start with a `?` or `#` before the userinfo `@`.
            ("postgres://app:1234/xsecret?more@pg/db", ["xsecret", "more"]),
            ("https://user:8080?hunter2@host/db", ["hunter2"]),
            ("https://user:12?34@host", ["12?34", "?34"]),
            ("https://user:8080#hunter2@host/db", ["hunter2"]),
            ("mongodb://alice@example.com:8080?hunter2@mongo/db", ["hunter2"]),
            ("redis://:8080?hunter2@redis:6379/0", ["hunter2"]),
            # Whitespace in the user name, and an `@` in a later query segment.
            ("https://alice bob:hunter2@host/db", ["hunter2"]),
            ("smtp://John Doe:hunter2@mail.internal:587", ["hunter2"]),
            ("https://user :hunter2@host/db", ["hunter2"]),
            ("https://us\ter:hunter2@host/db", ["hunter2"]),
            ("https://us\ner:hunter2@host/db", ["hunter2"]),
            ("//alice bob:hunter2@host/db", ["hunter2"]),
            ("/debug?db=https%3A%2F%2Falice%20bob%3Ahunter2%40host%2Fdb", ["hunter2"]),
            ("https://user:8080?page=1&hunter2@host/db", ["hunter2"]),
            ("https://user:8080?page=1&hunter2%40host/db", ["hunter2"]),
            ("https://user:8080?page=1;hunter2@host/db", ["hunter2"]),
            ("https://user:8080?page=1&x=2&hunter2@host/db", ["hunter2"]),
            ("https://user : hunter2@host/db", ["hunter2"]),
            ("https://user: 8080@host/db", ["8080@"]),
            ("https://user:8080 @host/db", ["8080 @"]),
            ("https://user:pw @ host/db", ["pw @"]),
            ("https://user:8080?page = 1&hunter2@host/db", ["hunter2"]),
            ("https://user:8080?page=1&amp;hunter2@host/db", ["hunter2"]),
            ("https://user:8080?q=a+b&hunter2@host/db", ["hunter2"]),
            ("https://john+doe:hunter2@host/db", ["hunter2"]),
            ("https://user:8080?page=1&%20hunter2@host/db", ["hunter2"]),
            ("https://user:8080#a=1&hunter2@host/db", ["hunter2"]),
            ("https://user:8080?a=1?hunter2@host/db", ["hunter2"]),
            ("https://user:8080?a=1#hunter2@host/db", ["hunter2"]),
            ("https://user:8080?page=1%26hunter2%40host/db", ["hunter2", "8080"]),
            ("https://user:8080?page=1&hunter2%2540host/db", ["hunter2"]),
            ("https://user:8080?page=1&hunter2@host/db?email=a@b.com", ["hunter2"]),
            ("https://user:8080?hunter2%40host/db", ["hunter2"]),
            ("/debug?db=postgres://app:1234?xsecret@pg/db", ["xsecret"]),
            # A user name that holds an `@`.
            ("mongodb://alice@example.com:hunter2@mongo:27017/db", ["hunter2"]),
            ("postgres://alice@example.com:sec/ret@pg/db", ["sec/ret", "sec"]),
            ("smtp://alice@example.com:hunter2@mail.internal:587", ["hunter2"]),
            ("//alice@example.com:hunter2@host/db", ["hunter2"]),
            ("mongodb://ops@company.com:s3cret@mongo1:27017,mongo2:27017/db", ["s3cret"]),
            ("https://alice@example.com:hunter2%3Fextra@host/db", ["hunter2", "extra"]),
            ("/debug?db=" + quote("mongodb://alice@example.com:hunter2@mongo:27017/db", safe=""), ["hunter2"]),
            # Spaces in the password.
            ("https://user:correct horse@host/db", ["correct", "horse"]),
            ("https://user:pass word@host/db?page=1", ["pass", "word"]),
            # A replaced host that had held `?code`, before a value starting with `?`.
            ("/login?returnTo%253D//user:?HEADX@TAILX@host/cb%253Fcode%3D?HEADX@TAILX", ["HEADX", "TAILX"]),
        ],
    )
    def test_the_secret_is_not_logged(self, url: str, secrets: list[str]) -> None:
        out = _decode_all(redact_sensitive_query_params(url))
        for secret in secrets:
            assert secret not in out, out

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:3001/invite?email=a@b.com",
            "http://localhost:3001?email=a@b.com",
            "http://localhost:3001/#/login?next=a@b.com",
            "http://localhost:3001?page=1&email=a@b.com",
            "https://user:8080?page=1&email=a@b.com",
            "http://localhost:3001?page=1&amp;email=a@b.com",
            "http://localhost:3001?email = a@b.com",
            "http://localhost:3001?q=a+b&email=a@b.com",
            "http://localhost:3001?page=1;email=a@b.com",
        ],
    )
    def test_a_port_with_an_at_sign_in_a_query_pair_is_still_a_port(self, url: str) -> None:
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
# It may also start with a separator or a port-like number, or hold spaces.
_PASSWORD_SECRETS = [
    *_SECRETS,
    "HEADX?TAILX",
    "HEADX#TAILX",
    "HEADX@TAILX",
    "/HEADX/TAILX",
    "?HEADX@TAILX",
    "#HEADX",
    "8080/HEADX",
    "8080%2FHEADX",
    "8080?HEADX",
    "8080#HEADX",
    "8080?page=1&HEADX",
    "8080?page=1;HEADX",
    "HEADX TAILX",
]
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
    # A user name that holds an `@`.
    "mongodb://alice@example.com:{s}@mongo:27017/db",
    "//alice@example.com:{s}@host/db",
    # A user name that holds whitespace.
    "https://alice bob:{s}@host.example/db",
    "smtp://John Doe:{s}@mail.internal:587",
    "//alice bob:{s}@host/db",
]
# Nested in a query, where a raw `#` would end the outer URL first.
_NESTED_PASSWORD_TEMPLATES = [
    "/debug?db=postgres://app:{s}@pg/db",
    "/debug?db=//user:{s}@host/db",
    "/login?returnTo=//user:{s}@host/cb?code={s}",
    "/debug?db=mongodb://alice@example.com:{s}@mongo:27017/db",
    "/debug?db=https://alice bob:{s}@host/db",
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
        # A raw `#` or `&` would end the outer URL or pair first.
        *(t.format(s=s) for t in _NESTED_PASSWORD_TEMPLATES for s in _PASSWORD_SECRETS if not {"#", "&"} & set(s)),
    ]
    checked = 0
    for template in cases:
        for url in _variants(template):
            out = _decode_all(redact_sensitive_query_params(url))
            for marker in _MARKERS:
                assert marker not in out, f"{url} -> {out}"
            checked += 1
    assert checked > 20000


# Each of these took seconds before (ReDoS); linear code takes a few ms. The
# bound is loose so a loaded CI runner cannot fail it.
_BOUND_MS = 200
_N = 50_000
_CAP = 8 * 1024
_TRUNCATED = "[Truncated]"
_FAKE_CODE = "4/0FAKE-auth-code-not-real"


def _elapsed_ms(run) -> float:
    started = time.perf_counter()
    run()
    return (time.perf_counter() - started) * 1000


# `^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$` in is_sensitive_query_key.
_TRIM_SHAPE = "a" + "/" * _N + "a"
# Joining the rest of a pair for every name of a long run of `=`.
_EQUALS_SHAPE = "a=b=c=d=e=token" + "=" * _N
# Many `//user:` heads, each followed by a port: the password-URL regex re-ran
# its port check to the end of the text for every head. The text stays under
# the logging cap, so it is redacted ten times over to keep the gap wide.
_PASSWORD_SHAPE = ",//a:1" * 1300 + "?=@"
# The same text with no `:port`, which the old regex read once.
_PASSWORD_CONTROL = _PASSWORD_SHAPE.replace(":", "-")
# Linear code takes ~1.5x as long on the shape as on the control, with or
# without coverage; the quadratic version took ~100x.
_MAX_SLOWDOWN = 10


def _ten_times(url: str) -> None:
    for _ in range(10):
        redact_sensitive_query_params(url)


class TestAdversarialInput:
    @pytest.mark.parametrize(
        "run",
        [
            pytest.param(lambda: is_sensitive_query_key(_TRIM_SHAPE), id="key-trim"),
            pytest.param(lambda: redact_sensitive_query_params(f"/x?{_TRIM_SHAPE}=1"), id="url-trim"),
            pytest.param(lambda: redact_sensitive_query_params(f"/x?{_EQUALS_SHAPE}"), id="url-equals"),
            pytest.param(lambda: redact_sensitive_query_params(f"/x?a{'=' * (_CAP - 8)}"), id="url-equals-under-cap"),
        ],
    )
    def test_finishes_in_linear_time(self, run) -> None:
        assert _elapsed_ms(run) < _BOUND_MS

    # A ratio, not a time bound: coverage slows this Python code ~3x and a
    # shared CI core slows it again, but both inputs alike. Fastest of three,
    # since load only ever adds time.
    @pytest.mark.parametrize(
        "prefix", ["/x?a=", "https://h.example/x?a="], ids=["url-password", "referer-password"],
    )
    def test_port_like_heads_cost_no_more_than_plain_text(self, prefix: str) -> None:
        shape = min(_elapsed_ms(lambda: _ten_times(prefix + _PASSWORD_SHAPE)) for _ in range(3))
        control = min(_elapsed_ms(lambda: _ten_times(prefix + _PASSWORD_CONTROL)) for _ in range(3))
        assert shape < control * _MAX_SLOWDOWN

    @pytest.mark.parametrize("key", ["..code..", "%00code", "__token__", "code\u212a"])
    def test_stray_characters_around_a_sensitive_name_are_trimmed(self, key: str) -> None:
        assert is_sensitive_query_key(key)

    @pytest.mark.parametrize("key", ["\u017ftate", "\u212aey"])
    def test_only_ascii_letters_make_a_sensitive_name(self, key: str) -> None:
        # `ſ` and the Kelvin sign case-fold to `s` and `k` under Unicode rules.
        assert not is_sensitive_query_key(key)

    def test_a_user_password_url_after_many_port_like_heads_is_still_read(self) -> None:
        out = redact_sensitive_query_params("/x?a=" + ",//a:1" * 200 + ",//u:hunter2@h")
        assert "hunter2" not in out

    def test_a_marker_followed_by_any_run_of_equals_counts_as_redacted(self) -> None:
        assert redact_sensitive_query_params("/cb?x=[REDACTED]===") == "/cb?x=[REDACTED]==="


class TestLoggingCap:
    def test_a_url_up_to_the_cap_is_logged_as_it_is(self) -> None:
        url = "/api/v1/x?q=" + "a" * (_CAP - 12)
        assert len(url) == _CAP
        assert redact_sensitive_query_params(url) == url

    def test_a_longer_url_is_cut_back_to_a_separator_and_marked(self) -> None:
        out = redact_sensitive_query_params("/api/v1/x?page=1&q=" + "a" * _CAP)
        assert out == f"/api/v1/x?page=1&q={_TRUNCATED}"

    def test_no_part_of_a_secret_the_cut_falls_inside_is_kept(self) -> None:
        out = redact_sensitive_query_params("/x?pad=" + "a" * (_CAP - 20) + "&code=" + "S" * 200)
        assert "SSS" not in out
        assert f"code={REDACTED}" in out
        assert out.endswith(_TRUNCATED)

    def test_a_secret_past_the_cut_is_dropped(self) -> None:
        out = redact_sensitive_query_params("/x?q=" + "a" * _CAP + "&code=" + _FAKE_CODE)
        assert "FAKE-auth-code" not in out

    @pytest.mark.parametrize("tail", ["@host/x", "%40host/x", "%2540host/x"])
    @pytest.mark.parametrize(
        "head",
        [
            pytest.param("https://user:p?x=1&" + "y" * _CAP, id="password-with-query"),
            pytest.param("https://user:" + "p" * _CAP, id="long-password"),
            pytest.param("/cb?next=https://u:p%3Fx%3D1%26y/" + "z" * _CAP, id="nested"),
        ],
    )
    def test_nothing_is_logged_of_a_url_whose_dropped_part_holds_an_at_sign(self, head: str, tail: str) -> None:
        assert redact_sensitive_query_params(head + tail) == f"{REDACTED}{_TRUNCATED}"
