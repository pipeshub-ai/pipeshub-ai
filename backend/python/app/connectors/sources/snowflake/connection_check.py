"""The Snowflake setup form's connection check.

One statement, sent directly with a short timeout and no retries, so the
answer that explains a failure is the one the user sees. Messages name what
to change in Snowflake. Codes marked (inferred) are not in the SQL API's
documentation and are matched by message text too; the live tests confirm
them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.connectors.core.base.connector.connector_service import ConnectionCheckResult
from app.sources.client.snowflake.snowflake import (
    AuthType,
    SnowflakeConnectionSettings,
    SnowflakeResponse,
    parse_connection_settings,
    sql_api_base_url,
)
from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource

if TYPE_CHECKING:
    from logging import Logger

CONNECTION_CHECK_TIMEOUT_S = 15.0
_STATEMENT_TIMEOUT_S = 10
CHECK_STATEMENT = (
    "SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME(), "
    "CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()"
)

_ROLE_NOT_GRANTED_CODES = frozenset({"390189"})  # (inferred)
_NETWORK_POLICY_CODES = frozenset({"390422"})  # INCOMING_REQUEST_BLOCKED (inferred)
_TIMEOUT_CODES = frozenset({"000630"})
_LOGIN_FAILURE_ID = re.compile(r"\[([0-9a-fA-F-]{8,})\]")

DataSourceFactory = Callable[[SnowflakeConnectionSettings], SnowflakeDataSource]


def _host(settings: SnowflakeConnectionSettings) -> str:
    return sql_api_base_url(settings.account_identifier).split("//", 1)[1].split("/", 1)[0]


def describe_failure(
    response: SnowflakeResponse, settings: SnowflakeConnectionSettings, fingerprint: str | None = None,
) -> str:
    """What went wrong, in terms of what to change in Snowflake or on this form."""
    text = response.error or ""
    lowered = text.lower()
    code = response.code or ""
    host = _host(settings)

    if response.status_code is None:
        if "timeout" in (response.exception_type or "").lower():
            return f"Snowflake at {host} did not answer within {int(CONNECTION_CHECK_TIMEOUT_S)} seconds."
        return f"Could not reach {host}: {text or response.exception_type or 'no answer'}."
    if code in _ROLE_NOT_GRANTED_CODES or ("role" in lowered and ("not granted" in lowered or "does not exist" in lowered)):
        return (
            f"The role {settings.role or '(default)'} is not granted to the user, or does not exist. "
            f"Grant it (GRANT ROLE … TO USER …) or leave Role empty. Snowflake said: {text}"
        )
    if code in _NETWORK_POLICY_CODES or ("network policy" in lowered or "incoming request" in lowered):
        return (
            "The user's network policy blocks this server. Allow the egress addresses shown on this form "
            f"in the policy. Snowflake said: {text}"
        )
    if response.status_code == 404:
        return (
            f"No Snowflake account answers at {host}. Check the account identifier: orgname-accountname "
            "(for example acme-prod) or the account locator with its region (for example xy12345.us-east-1)."
        )
    if response.status_code == 401:
        return _rejected_credentials(text, settings, fingerprint)
    if response.status_code == 403:
        return (
            "Snowflake refused the request: the SQL API may not be enabled for the account, or a network "
            f"policy blocks this server's addresses (shown on this form). Snowflake said: {text or 'forbidden'}"
        )
    if response.status_code == 408 or code in _TIMEOUT_CODES or response.sql_state == "57014":
        return "Snowflake did not finish the test query in time; the warehouse may be starting or busy. Try again."
    if response.status_code == 429:
        return "Snowflake is limiting requests from this account right now. Try again in a minute."
    return f"Snowflake refused the connection (HTTP {response.status_code}): {text or 'no details'}"


def _rejected_credentials(text: str, settings: SnowflakeConnectionSettings, fingerprint: str | None) -> str:
    if settings.auth_type != AuthType.KEY_PAIR:
        return (
            "Snowflake rejected the personal access token. Check that it has not expired or been revoked, "
            "and that the user has a network policy allowing this server, which PATs require. "
            f"Snowflake said: {text or 'unauthorized'}"
        )
    user = settings.user.strip().upper()
    hint = (
        f"Check that {user} is the user's LOGIN_NAME and that its public key matches this private key: "
        f"this key's fingerprint is {fingerprint}; compare it with RSA_PUBLIC_KEY_FP in DESC USER."
    )
    failure_id = _LOGIN_FAILURE_ID.search(text)
    if failure_id:
        hint += (
            " An ACCOUNTADMIN can see the reason with "
            f"SELECT SYSTEM$GET_LOGIN_FAILURE_DETAILS('{failure_id.group(1)}')."
        )
    return f"Snowflake rejected the key-pair login for {user}. {hint} Snowflake said: {text or 'unauthorized'}"


def _row(response: SnowflakeResponse) -> list[Any] | None:
    data = response.data if isinstance(response.data, dict) else {}
    rows = data.get("data") or []
    return rows[0] if rows and isinstance(rows[0], list) and len(rows[0]) >= 4 else None


def _default_data_source(settings: SnowflakeConnectionSettings) -> SnowflakeDataSource:
    return SnowflakeDataSource(settings.create_client(timeout=CONNECTION_CHECK_TIMEOUT_S))


async def check_connection(
    auth_config: dict[str, Any],
    logger: Logger,
    *,
    data_source_factory: DataSourceFactory = _default_data_source,
) -> ConnectionCheckResult:
    """Try the setup form's auth settings with one statement."""
    try:
        settings = parse_connection_settings({"auth": auth_config})
        fingerprint = settings.signer().fingerprint if settings.auth_type == AuthType.KEY_PAIR else None
        data_source = data_source_factory(settings)
    except ValueError as e:
        return ConnectionCheckResult(success=False, message=str(e))

    try:
        response = await data_source.execute_sql(
            CHECK_STATEMENT,
            warehouse=settings.warehouse,
            role=settings.role,
            timeout=_STATEMENT_TIMEOUT_S,
            parameters={"MULTI_STATEMENT_COUNT": "1", "QUERY_TAG": "pipeshub:connection-check"},
        )
    finally:
        await data_source.http.close()

    if not response.success:
        logger.info(
            "Snowflake connection check to %s failed: status=%s code=%s", _host(settings),
            response.status_code, response.code,
        )
        return ConnectionCheckResult(success=False, message=describe_failure(response, settings, fingerprint))
    row = _row(response)
    if row is None:
        if response.status_code == 202:  # the statement outlived the synchronous wait
            return ConnectionCheckResult(
                success=False,
                message="Snowflake did not finish the test query in time; the warehouse may be starting. Try again.",
            )
        return ConnectionCheckResult(
            success=False, message="Snowflake answered the test query with an unexpected result. Try again.",
        )
    account, user, role, warehouse = row[:4]
    # An account outside an organization has no organization name.
    account = account or "(this account)"
    if not warehouse:
        return ConnectionCheckResult(
            success=False,
            message=(
                f"Connected as {user}, but the warehouse {settings.warehouse} does not exist or the role "
                f"{role} has no USAGE on it. Grant USAGE ON WAREHOUSE {settings.warehouse} to the role."
            ),
        )
    return ConnectionCheckResult(
        success=True, message=f"Connected to account {account} as {user}, role {role}, warehouse {warehouse}.",
    )
