"""Which bearer-token classes the Python services accept, and how routes tell them apart.

Two secrets sign the tokens that reach these services:

* ``jwtSecret`` — user sessions and OAuth/PAT access tokens ("regular" tokens).
* ``scopedJwtSecret`` — service-to-service tokens ("scoped" tokens). The same secret
  also signs single-purpose tokens that belong to Node (password reset, refresh, mail,
  storage, user lookup, ...), so a valid signature alone proves nothing about intent:
  a scoped token is only accepted when it carries one of ``ACCEPTED_SERVICE_SCOPES``,
  and a route only admits it when the route opts in for that scope.
"""

from collections.abc import Mapping
from enum import Enum
from typing import Any, Final, cast

from app.config.constants.service import TokenScopes


class AuthTokenType(str, Enum):
    REGULAR = "regular"
    SCOPED = "scoped"


ScopeLike = TokenScopes | str

ACCEPTED_SERVICE_SCOPES: Final[frozenset[str]] = frozenset(
    {
        TokenScopes.CONNECTOR_SIGNED_URL.value,  # indexing -> connectors record stream
        TokenScopes.RECORD_CONTENT.value,  # query -> connectors ACL-checked record content
        TokenScopes.CONVERSATION_CREATE.value,  # Node Slack service account -> query chat
        TokenScopes.FETCH_CONFIG.value,  # Node scheduled-jobs backfill -> connectors
    }
)


def scope_value(scope: ScopeLike) -> str:
    return scope.value if isinstance(scope, TokenScopes) else scope


def token_scopes(claims: Mapping[str, Any]) -> frozenset[str]:
    """Scopes from the service-token ``scopes`` claim.

    OAuth ``scope``/``oauthScopes`` are deliberately ignored: they grant API access to a
    user's OAuth client and must never be read as service privileges.
    """
    raw: object = claims.get("scopes")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(scope for scope in cast("list[object]", raw) if isinstance(scope, str))


def is_service_token(user: Mapping[str, Any] | None) -> bool:
    return user is not None and user.get("token_type") == AuthTokenType.SCOPED.value


def has_service_scope(user: Mapping[str, Any] | None, *scopes: ScopeLike) -> bool:
    if user is None or not is_service_token(user):
        return False
    wanted = {scope_value(scope) for scope in scopes}
    return bool(token_scopes(user) & wanted)
