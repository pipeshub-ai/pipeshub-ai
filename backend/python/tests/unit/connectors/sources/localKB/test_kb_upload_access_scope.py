"""An upload-only OAuth token passes the access check Node runs before an upload.

Node gates POST /knowledgeBase/:kbId/upload with kb:upload, then asks this service
whether the caller can write to that knowledge base (GET /{kb_id}), forwarding the
caller\x27s token. If that lookup admitted only kb:read, a token granted exactly what
the upload route asks for would be refused there with 403.
"""

from fastapi.routing import APIRoute

# app.edition_config and app.api.routes.search import each other; load it first.
import app.edition_config  # noqa: F401
from app.api.middlewares.auth import AUTH_POLICY_ATTR
from app.config.constants.service import OAuthScopes
from app.connectors.sources.localKB.api.kb_router import kb_router


def _oauth_scopes(method: str, path: str) -> set[str]:
    route = next(
        r
        for r in kb_router.routes
        if isinstance(r, APIRoute)
        and method in (r.methods or ())
        and r.path.removeprefix(kb_router.prefix) == path
    )
    scopes: set[str] = set()
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        policy = getattr(dependant.call, AUTH_POLICY_ATTR, None)
        if policy is not None:
            scopes |= {getattr(s, "value", s) for s in policy.oauth_scopes}
        pending.extend(dependant.dependencies)
    return scopes


def test_knowledge_base_lookup_admits_read_and_upload_tokens() -> None:
    assert _oauth_scopes("GET", "/{kb_id}") == {
        OAuthScopes.KB_READ.value,
        OAuthScopes.KB_UPLOAD.value,
    }


def test_listing_knowledge_bases_still_needs_read() -> None:
    assert _oauth_scopes("GET", "/") == {OAuthScopes.KB_READ.value}
