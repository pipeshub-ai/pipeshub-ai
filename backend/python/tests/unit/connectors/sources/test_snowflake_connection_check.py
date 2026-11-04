"""SnowflakeConnector.check_connection: the setup form's one-statement check."""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.connectors.sources.snowflake.connection_check import (
    CHECK_STATEMENT,
    check_connection,
    describe_failure,
)
from app.connectors.sources.snowflake.connector import SnowflakeConnector
from app.sources.client.snowflake.snowflake import (
    SnowflakeResponse,
    parse_connection_settings,
)

_LOGGER = logging.getLogger("test.snowflake.check")
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
).decode()

PAT = {"authType": "ACCESS_KEY", "accountIdentifier": "acme-prod", "warehouse": "WH", "patToken": "pat-secret"}
KEY_PAIR = {"authType": "KEY_PAIR", "accountIdentifier": "acme-prod", "warehouse": "WH", "user": "svc",
            "privateKey": PEM, "role": "LOADER"}


def ok(row) -> SnowflakeResponse:
    return SnowflakeResponse(success=True, status_code=200, data={"data": [row]})


def failure(status, message="", code=None, **extra) -> SnowflakeResponse:
    return SnowflakeResponse(success=False, status_code=status, error=message, code=code, **extra)


class Factory:
    def __init__(self, response: SnowflakeResponse) -> None:
        self.data_source = MagicMock()
        self.data_source.execute_sql = AsyncMock(return_value=response)
        self.data_source.http.close = AsyncMock()

    def __call__(self, settings: object) -> object:
        return self.data_source


async def run(auth, response) -> tuple:
    factory = Factory(response)
    result = await check_connection(auth, _LOGGER, data_source_factory=factory)
    return result, factory.data_source


class TestCheck:
    async def test_success_names_account_user_role_and_warehouse(self) -> None:
        result, ds = await run(KEY_PAIR, ok(["ACME-PROD", "SVC", "LOADER", "WH"]))
        assert result.success and result.message == "Connected to account ACME-PROD as SVC, role LOADER, warehouse WH."
        (call,) = ds.execute_sql.await_args_list
        assert call.args == (CHECK_STATEMENT,)
        assert call.kwargs["role"] == "LOADER" and call.kwargs["warehouse"] == "WH"
        assert call.kwargs["parameters"]["MULTI_STATEMENT_COUNT"] == "1"
        ds.http.close.assert_awaited_once()

    async def test_a_warehouse_the_role_cannot_use_is_a_failure(self) -> None:
        result, _ = await run(PAT, ok(["ACME-PROD", "SVC", "PUBLIC", None]))
        assert not result.success and "USAGE ON WAREHOUSE WH" in result.message

    async def test_a_statement_still_running_is_a_failure(self) -> None:
        result, _ = await run(PAT, SnowflakeResponse(success=True, status_code=202, data={"statementHandle": "h"}))
        assert not result.success and "in time" in result.message

    async def test_invalid_settings_fail_without_a_request(self) -> None:
        factory = Factory(ok([]))
        result = await check_connection({**PAT, "patToken": ""}, _LOGGER, data_source_factory=factory)
        assert not result.success and "personal access token" in result.message
        factory.data_source.execute_sql.assert_not_awaited()

    async def test_a_failed_check_closes_its_client_and_logs_no_secret(self, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        result, ds = await run(PAT, failure(401, "Invalid token", code="390303"))
        assert not result.success
        ds.http.close.assert_awaited_once()
        assert "pat-secret" not in caplog.text and "pat-secret" not in result.message

    async def test_the_create_form_without_auth_type_is_key_pair_when_it_has_a_key(self) -> None:
        auth = {k: v for k, v in KEY_PAIR.items() if k != "authType"}
        result, _ = await run(auth, ok(["ACME-PROD", "SVC", "LOADER", "WH"]))
        assert result.success

    async def test_the_connector_offers_it(self) -> None:
        assert SnowflakeConnector.supports_connection_check()


def settings(auth=PAT) -> object:
    return parse_connection_settings({"auth": auth})


class TestMessages:
    def test_unknown_account(self) -> None:
        message = describe_failure(failure(404, "<html>"), settings())
        assert re.search(r"\bacme-prod\.snowflakecomputing\.com\b", message) and "orgname-accountname" in message

    def test_rejected_pat(self) -> None:
        assert "personal access token" in describe_failure(failure(401, "Invalid OAuth access token"), settings())

    def test_rejected_key_pair_gives_the_fingerprint_and_the_failure_lookup(self) -> None:
        auth = settings(KEY_PAIR)
        message = describe_failure(
            failure(401, "JWT token is invalid. [0ce9eb56-821d-4ca9-a774-04ae89a0cf5a]", code="390144"),
            auth, auth.signer().fingerprint,
        )
        assert auth.signer().fingerprint in message and "RSA_PUBLIC_KEY_FP" in message and "LOGIN_NAME" in message
        assert "SYSTEM$GET_LOGIN_FAILURE_DETAILS('0ce9eb56-821d-4ca9-a774-04ae89a0cf5a')" in message
        assert "PRIVATE KEY" not in message

    @pytest.mark.parametrize(("response", "says"), [
        (failure(403, "SQL API not enabled"), "SQL API may not be enabled"),
        (failure(422, "Role 'LOADER' specified in the connect string is not granted to this user.", code="390189"),
         "not granted"),
        (failure(403, "Incoming request with IP/Token 1.2.3.4 is not allowed to access Snowflake.",
                 code="390422"), "network policy"),
        (failure(408, "timeout"), "in time"),
        (failure(422, "Statement reached its statement timeout", sql_state="57014"), "in time"),
        (failure(429, "too many"), "limiting"),
        (failure(500, "boom"), "HTTP 500"),
        (SnowflakeResponse(success=False, error="ConnectTimeout", exception_type="ConnectTimeout"),
         "did not answer within"),
        (SnowflakeResponse(success=False, error="Name or service not known", exception_type="ConnectError"),
         "Could not reach"),
    ])
    def test_each_failure_says_what_to_change(self, response, says) -> None:
        assert says in describe_failure(response, settings())
