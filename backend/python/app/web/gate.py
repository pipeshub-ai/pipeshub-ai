from app.utils.url_fetcher import FetchError, check_http_url_without_resolving
from app.web.ports import AccessDecision, AccessRequest


class DefaultWebAccessGate:
    """Any search; a fetch of any http(s) URL whose host is not internal by its spelling alone.
    The address a host resolves to is checked again when it is fetched."""

    async def check(self, request: AccessRequest) -> AccessDecision:
        if request.action == "search":
            return AccessDecision(True)
        try:
            check_http_url_without_resolving(request.target)
        except FetchError as e:
            return AccessDecision(False, str(e))
        return AccessDecision(True)
