"""Who is behind an OAuth access token, read from how the token was minted.

Every OAuth access token comes out of the Node API's `OAuthTokenService.generateTokens`,
and the minting path leaves two signed claims that say where it came from:

- `client_id`: the client it was minted against. Service tokens, and the delegated tokens
  exchanged from them, are minted against the org's own `svc-system:<orgId>` client
  (`service-token.service.ts` `serviceTokenClientId`). No OAuth app can be registered under
  that prefix: app ids are random UUIDs, metadata-document URLs or other reserved prefixes.
- `act`: set only on a delegated token (`token-exchange.service.ts`), where `userId` is the
  person the service account acts as and `act.sub` is the service account.

So a token is classified by provenance, not by comparing identity fields:

- a delegated token (`svc-system:` client, `act.sub` set, `userId` the person) is that person;
- any other `svc-system:` token is the service account's own token: no person is present;
- a client_credentials token (`userId` == `client_id`) is an app: no person is present;
- everything else (authorization code, device code, personal access token) is a person.

A token carrying `act` in any other shape was not minted by Node, so its kind is unknown
and the auth layer refuses it.

The Node API reads tokens with the same rules (`oauth_provider/utils/principal-kind.ts`).
Both are tested against the shapes Node actually mints
(`backend/nodejs/apps/tests/modules/oauth_provider/fixtures/minted-token-shapes.json`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

#: A person: a session, a personal access token, an authorization-code or
#: device-code token, or a service account's delegated token acting as a person.
PRINCIPAL_HUMAN = "human"
#: A client_credentials token: an application with no person behind it.
PRINCIPAL_APP = "app"
#: A service account's own token: an unattended process with no person behind it.
PRINCIPAL_SERVICE_ACCOUNT = "service_account"
#: A service-to-service token (`scopedJwtSecret`).
PRINCIPAL_SERVICE = "service"
#: A token whose shape no minting path produces.
PRINCIPAL_UNKNOWN = "unknown"

#: The kinds that name a person who may be answered as.
PERSON_KINDS = frozenset({PRINCIPAL_HUMAN})

#: The client every service token and delegated token is minted against (Node
#: `SERVICE_TOKEN_APP_CLIENT_ID_PREFIX`).
SERVICE_TOKEN_CLIENT_ID_PREFIX = "svc-system:"

OAuthPrincipalKind = Literal["human", "app", "service_account", "unknown"]


def _is_delegation(claims: Mapping[str, Any], *, service_client: bool) -> bool:
    act = claims.get("act")
    if not service_client or not isinstance(act, Mapping):
        return False
    actor = act.get("sub")
    user_id = claims.get("userId")
    return (
        isinstance(actor, str)
        and actor != ""
        and isinstance(user_id, str)
        and user_id != ""
        and user_id != actor
    )


def oauth_principal_kind(claims: Mapping[str, Any]) -> OAuthPrincipalKind:
    """The kind of principal behind a verified OAuth access token's claims."""
    client_id = claims.get("client_id")
    service_client = isinstance(client_id, str) and client_id.startswith(
        SERVICE_TOKEN_CLIENT_ID_PREFIX
    )
    if "act" in claims:
        return (
            PRINCIPAL_HUMAN
            if _is_delegation(claims, service_client=service_client)
            else PRINCIPAL_UNKNOWN
        )
    if service_client:
        return PRINCIPAL_SERVICE_ACCOUNT
    user_id = claims.get("userId")
    if isinstance(user_id, str) and user_id != "" and user_id == client_id:
        return PRINCIPAL_APP
    return PRINCIPAL_HUMAN
