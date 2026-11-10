"""Key-pair auth for the Snowflake SQL API: keys, claims, re-signing, settings."""

import base64
import hashlib
import logging
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.snowflake import keypair
from app.sources.client.snowflake.keypair import (
    REFRESH_MARGIN_S,
    TOKEN_LIFETIME_S,
    KeyPairSigner,
    account_host_label,
    jwt_account,
    load_private_key,
    public_key_fingerprint,
)
from app.sources.client.snowflake.snowflake import (
    AuthType,
    SnowflakeRESTClientViaKeyPair,
    parse_connection_settings,
)
from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key=_KEY, *, passphrase: str | None = None, fmt=serialization.PrivateFormat.PKCS8) -> str:
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode()) if passphrase
        else serialization.NoEncryption()
    )
    return key.private_bytes(serialization.Encoding.PEM, fmt, encryption).decode()


PEM = pem()


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestAccount:
    @pytest.mark.parametrize(("identifier", "label", "account"), [
        ("acme-prod", "acme-prod", "ACME-PROD"),
        ("xy12345.us-east-1", "xy12345.us-east-1", "XY12345"),
        ("xy12345.us-east-2.aws", "xy12345.us-east-2.aws", "XY12345"),
        ("xy12345.east-us-2.azure", "xy12345.east-us-2.azure", "XY12345"),
        ("acme-prod.privatelink", "acme-prod.privatelink", "ACME-PROD"),
        ("https://acme-prod.snowflakecomputing.com/console", "acme-prod", "ACME-PROD"),
        ("acme-prod.snowflakecomputing.com", "acme-prod", "ACME-PROD"),
        ("xy12345", "xy12345", "XY12345"),
        ("acme-prod.global", "acme-prod.global", "ACME"),
        ("xy12345.fhplus.us-gov-west-1.aws", "xy12345.fhplus.us-gov-west-1.aws", "XY12345"),
        ("xy12345.dod.us-gov-west-1.aws", "xy12345.dod.us-gov-west-1.aws", "XY12345"),
        ("xy12345.fhplus.us-gov-virginia.azure", "xy12345.fhplus.us-gov-virginia.azure", "XY12345"),
        ("xy12345.mexicocentral.azure", "xy12345.mexicocentral.azure", "XY12345"),
        ("XY12345.SNOWFLAKECOMPUTING.COM", "XY12345", "XY12345"),
    ])
    def test_host_label_and_jwt_account(self, identifier, label, account) -> None:
        assert account_host_label(identifier) == label
        assert jwt_account(identifier) == account

    @pytest.mark.parametrize("identifier", ["acme.prod", "", "acme prod", "acme;drop"])
    def test_unusable_identifiers_are_rejected_with_a_hint(self, identifier) -> None:
        with pytest.raises(ValueError) as error:
            account_host_label(identifier)
        if identifier == "acme.prod":
            assert "acme-prod" in str(error.value)


class TestKeys:
    def test_pkcs8_pkcs1_encrypted_and_one_line_keys_load(self) -> None:
        assert load_private_key(PEM).key_size == 2048
        assert load_private_key(pem(fmt=serialization.PrivateFormat.TraditionalOpenSSL))
        assert load_private_key(pem(passphrase="s3cret"), "s3cret")
        one_line = PEM.replace("\n", " ")
        assert load_private_key(one_line).key_size == 2048

    @pytest.mark.parametrize(("key", "passphrase", "says"), [
        (pem(passphrase="s3cret"), None, "enter its passphrase"),
        (pem(passphrase="s3cret"), "wrong", "check the passphrase"),
        (PEM, "unneeded", "not encrypted"),
        ("not a key", None, "whole PEM"),
        ("", None, "missing"),
        (pem(ec.generate_private_key(ec.SECP256R1())), None, "RSA"),
    ], ids=["no-passphrase", "wrong-passphrase", "unneeded-passphrase", "not-a-key", "missing", "not-rsa"])
    def test_unusable_keys_say_why_and_never_echo_the_key(self, key, passphrase, says) -> None:
        with pytest.raises(ValueError) as error:
            load_private_key(key, passphrase)
        assert says in str(error.value)
        assert "MII" not in str(error.value) and error.value.__cause__ is None

    def test_a_short_key_says_how_many_bits_snowflake_needs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A higher floor, not a weaker key: no test generates a key below 2048 bits.
        monkeypatch.setattr(keypair, "MIN_KEY_BITS", 4096)
        with pytest.raises(ValueError, match="2048 bits; Snowflake needs at least 4096"):
            load_private_key(PEM)

    def test_fingerprint_is_sha256_of_the_public_key(self) -> None:
        der = _KEY.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        expected = "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()
        assert public_key_fingerprint(_KEY) == expected


class TestSigner:
    def test_claims_follow_snowflakes_recipe_and_verify(self) -> None:
        clock = Clock()
        signer = KeyPairSigner("xy12345.us-east-1", "svc_pipeshub", _KEY, clock=clock)
        claims = jwt.decode(signer.token(), _KEY.public_key(), algorithms=["RS256"],
                            options={"verify_exp": False, "verify_iat": False})
        assert claims["sub"] == "XY12345.SVC_PIPESHUB"
        assert claims["iss"] == f"XY12345.SVC_PIPESHUB.{public_key_fingerprint(_KEY)}"
        assert claims["exp"] - claims["iat"] == TOKEN_LIFETIME_S <= 3600

    def test_a_token_is_reused_until_near_expiry(self) -> None:
        clock = Clock()
        signer = KeyPairSigner("acme-prod", "u", _KEY, clock=clock)
        first = signer.token()
        clock.now += 10 * 60
        assert signer.token() == first
        clock.now += TOKEN_LIFETIME_S - REFRESH_MARGIN_S
        assert signer.token() != first

    def test_repr_holds_no_key_or_token(self) -> None:
        signer = KeyPairSigner("acme-prod", "u", _KEY)
        token = signer.token()
        assert token not in repr(signer) and "PRIVATE" not in repr(signer)

    def test_a_user_is_required(self) -> None:
        with pytest.raises(ValueError, match="user"):
            KeyPairSigner("acme-prod", " ", _KEY)


def key_pair_config(**extra) -> dict:
    return {"auth": {"authType": "KEY_PAIR", "accountIdentifier": "acme-prod", "warehouse": "WH",
                     "user": "svc", "privateKey": PEM, **extra}}


class TestSettings:
    def test_key_pair_settings_and_secrets(self) -> None:
        settings = parse_connection_settings(key_pair_config(role="LOADER"))
        assert settings.auth_type == AuthType.KEY_PAIR and settings.role == "LOADER" and settings.user == "svc"
        assert "PRIVATE KEY" not in repr(settings) and "MII" not in repr(settings)

    def test_auth_type_mapping(self) -> None:
        pat = {"auth": {"accountIdentifier": "acme-prod", "warehouse": "WH", "patToken": "t"}}
        assert parse_connection_settings(pat).auth_type == AuthType.PAT
        assert parse_connection_settings({"auth": {**pat["auth"], "authType": "ACCESS_KEY"}}).auth_type == AuthType.PAT
        assert parse_connection_settings({"auth": {**pat["auth"], "authType": "PAT"}}).auth_type == AuthType.PAT
        inferred = key_pair_config()
        del inferred["auth"]["authType"]
        assert parse_connection_settings(inferred).auth_type == AuthType.KEY_PAIR
        with pytest.raises(ValueError, match="OAUTH"):
            parse_connection_settings({"auth": {**pat["auth"], "authType": "OAUTH"}})

    def test_auth_that_is_not_an_object_is_a_clear_error(self) -> None:
        with pytest.raises(ValueError, match="no auth settings"):
            parse_connection_settings({"auth": "not-a-dict"})

    def test_empty_role_means_the_default_role(self) -> None:
        assert parse_connection_settings(key_pair_config(role="  ")).role is None

    @pytest.mark.parametrize(("extra", "says"), [
        ({"user": ""}, "user"),
        ({"privateKey": "junk"}, "whole PEM"),
        ({"accountIdentifier": "acme.prod"}, "acme-prod"),
    ])
    def test_a_bad_key_pair_form_fails_before_any_request(self, extra, says) -> None:
        with pytest.raises(ValueError, match=says):
            parse_connection_settings(key_pair_config(**extra))

    def test_the_client_is_key_pair(self) -> None:
        client = parse_connection_settings(key_pair_config()).create_client().get_client()
        assert isinstance(client, SnowflakeRESTClientViaKeyPair)
        assert client.headers["X-Snowflake-Authorization-Token-Type"] == "KEYPAIR_JWT"
        assert client.get_base_url() == "https://acme-prod.snowflakecomputing.com/api/v2"


class TestReSigning:
    async def test_every_request_carries_a_current_token_even_from_a_stale_header_copy(self, monkeypatch) -> None:
        from app.sources.client.http import http_client

        sent: list[str] = []

        async def fake_execute(self, request, **kwargs) -> object:
            sent.append(request.headers["Authorization"])
            response = MagicMock()
            response.status = 200
            response.text.return_value = "{}"
            response.json.return_value = {}
            return response

        monkeypatch.setattr(http_client.HTTPClient, "execute", fake_execute)
        clock = Clock()
        client = SnowflakeRESTClientViaKeyPair("acme-prod", KeyPairSigner("acme-prod", "u", _KEY, clock=clock))
        data_source = SnowflakeDataSource(MagicMock(get_client=MagicMock(return_value=client)))
        await data_source.execute_sql("SELECT 1")
        clock.now += TOKEN_LIFETIME_S
        await data_source.get_statement_status("handle")
        await data_source.execute_sql("SELECT 2")
        assert len(sent) == 3 and sent[0] != sent[1] and sent[1] == sent[2]
        assert client.headers["Authorization"] == sent[2]

    async def test_a_token_never_reaches_the_logs(self, caplog, monkeypatch) -> None:
        caplog.set_level(logging.DEBUG)
        settings = parse_connection_settings(key_pair_config())
        token = settings.signer().token()
        assert token not in caplog.text and "PRIVATE KEY" not in caplog.text


def test_http_request_headers_are_mutable() -> None:
    request = HTTPRequest(method="GET", url="https://x", headers={"Authorization": "old"})
    request.headers["Authorization"] = "new"
    assert request.headers["Authorization"] == "new"


def test_the_api_url_ignores_the_domains_case() -> None:
    from app.sources.client.snowflake.snowflake import sql_api_base_url

    assert sql_api_base_url("XY12345.SNOWFLAKECOMPUTING.COM") == "https://XY12345.snowflakecomputing.com/api/v2"


def test_saved_pat_connectors_on_every_documented_locator_still_parse() -> None:
    for account in ("xy12345.fhplus.us-gov-west-1.aws", "xy12345.mexicocentral.azure", "acme-prod"):
        config = {"auth": {"authType": "ACCESS_KEY", "accountIdentifier": account, "warehouse": "WH", "patToken": "t"}}
        assert parse_connection_settings(config).account_identifier == account
