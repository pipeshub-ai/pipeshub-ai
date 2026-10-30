# pyright: ignore-file

"""Live ServiceNow fixtures (README.md in this folder says what the instance needs).

Session:
- ``sn_rest``: the admin's Table API client; sweeps objects of earlier runs older than STALE_HOURS.
- ``sn_personas``: the instance's test users, each with a PipesHub account under the same email and
  the instance read as them (the permission oracle). Created before any connector, so the first
  user sync already links them.
- ``sn_oauth_config``: the OAuth app every test connector signs in with, deleted at the end.

Each test module seeds its own objects (``sn_seed`` gives it a ``ServiceNowSeed`` that is torn
down with the module) and opens its own connectors with ``servicenow_connector``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.servicenow.constants import (
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
    ENV_INSTANCE_URL,
    NO_EMAIL_PERSONA,
    PERSONAS,
    REQUIRED_ENV,
    SERVICENOW_CONNECTOR_TYPE,
    persona_email,
    persona_password_env,
    persona_user_name,
)
from connectors.servicenow.servicenow_helpers import Persona
from connectors.servicenow.servicenow_seed import (
    ServiceNowError,
    ServiceNowRest,
    ServiceNowSeed,
    rest_from_env,
    sweep_stale,
)
from helper.clients.connectors_client import ConnectorOAuthConfigsClient
from helper.second_user import SecondUser, delete_second_user, source_user_account
from helper.source_credentials import missing_env, source_unavailable

logger = logging.getLogger("servicenow-conftest")


@pytest.fixture(scope="session")
def sn_rest() -> ServiceNowRest:
    missing = missing_env(REQUIRED_ENV)
    if missing:
        source_unavailable("The ServiceNow test instance is not configured.", secrets=missing)
    rest = rest_from_env()
    try:
        rest.count("kb_knowledge_base", "")
    except (ServiceNowError, OSError) as e:
        # A developer instance that sleeps answers with a login page or a redirect, not JSON.
        pytest.fail(f"The ServiceNow test instance did not answer as admin ({e}). Wake it up or check the credentials.")
    sweep_stale(rest)
    return rest


@pytest.fixture(scope="session")
def sn_personas(sn_rest: ServiceNowRest, pipeshub_client: PipeshubClient) -> dict[str, Persona]:
    """Every persona by name; ``noemail`` has neither a password nor a PipesHub account."""
    missing = missing_env([persona_password_env(name) for name in PERSONAS])
    if missing:
        source_unavailable("The ServiceNow test users' passwords are not configured.", secrets=missing)
    pipeshub_client._ensure_access_token()
    out: dict[str, Persona] = {}
    for name in (*PERSONAS, NO_EMAIL_PERSONA):
        row = sn_rest.one("sys_user", f"user_name={persona_user_name(name)}", "sys_id,email")
        if row is None:
            pytest.fail(f"The ServiceNow instance has no user {persona_user_name(name)!r}; see the suite README.")
        if name == NO_EMAIL_PERSONA:
            out[name] = Persona(name, row["sys_id"], "", None, None)
            continue
        email = persona_email(name)
        # Kept between runs, not deleted: an email whose account was deleted cannot join the org again.
        account = source_user_account(pipeshub_client, email, f"ServiceNow IT {name}")
        rest = sn_rest.as_user(persona_user_name(name), os.environ[persona_password_env(name)])
        out[name] = Persona(name, row["sys_id"], email, rest, account)
    return out


@pytest.fixture(scope="session")
def sn_oauth_config(sn_rest: ServiceNowRest, pipeshub_client: PipeshubClient) -> Iterator[str]:
    base = os.environ[ENV_INSTANCE_URL].strip().rstrip("/")
    api = ConnectorOAuthConfigsClient(pipeshub_client)
    resp = api.create_config(SERVICENOW_CONNECTOR_TYPE, {
        "oauthInstanceName": f"servicenow-it-{uuid.uuid4().hex[:8]}",
        "config": {
            "instanceUrl": base,
            "authorizeUrl": f"{base}/oauth_auth.do",
            "tokenUrl": f"{base}/oauth_token.do",
            "clientId": os.environ[ENV_CLIENT_ID],
            "clientSecret": os.environ[ENV_CLIENT_SECRET],
        },
    })
    assert resp.status_code in (200, 201), f"creating the ServiceNow OAuth app answered HTTP {resp.status_code}"
    oauth = resp.json().get("oauthConfig") or {}
    config_id = str(oauth.get("_id") or oauth.get("id") or "")
    assert config_id, "creating the ServiceNow OAuth app returned no id"
    try:
        yield config_id
    finally:
        resp = api.delete_config(SERVICENOW_CONNECTOR_TYPE, config_id)
        if resp.status_code >= 300:
            logger.warning("Deleting the ServiceNow test OAuth app answered HTTP %s", resp.status_code)


@pytest.fixture(scope="module")
def sn_seed(sn_rest: ServiceNowRest) -> Iterator[ServiceNowSeed]:
    """This module's own objects on the instance, removed when the module ends."""
    seed = ServiceNowSeed(sn_rest)
    try:
        yield seed
    finally:
        try:
            seed.teardown()
        except Exception:
            logger.exception("ServiceNow teardown of run %s failed; the next run sweeps it", seed.run)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def sn_accounts(pipeshub_client: PipeshubClient) -> AsyncIterator[dict[str, SecondUser]]:
    """PipesHub accounts for this module's throw-away instance users (unique emails, deleted at the end).

    Fill it with ``servicenow_helpers.add_account``.
    """
    accounts: dict[str, SecondUser] = {}
    yield accounts
    for user in accounts.values():
        await asyncio.to_thread(delete_second_user, pipeshub_client, user)

