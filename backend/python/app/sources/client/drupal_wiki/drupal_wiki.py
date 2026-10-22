import logging
import re
from dataclasses import asdict, dataclass
from http.cookiejar import DefaultCookiePolicy
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient
from app.sources.client.resilience import ResiliencePolicy
from app.utils.url_fetcher import vetted_addresses
from app.utils.url_redaction import redact_urls_in_text

# Drupal Wiki only offers personal access tokens: its OpenAPI spec declares a single
# bearer scheme ("PAT") and there is no OAuth or basic-auth endpoint.
SUPPORTED_AUTH_TYPES = ("API_TOKEN",)
API_VERSION = "1"
# Plain JSON only. The spec never mentions a vendor media type, so anything more is
# a guess that can only earn a 406.
ACCEPT_HEADER = "application/json"
_API_SUFFIXES = ("/api/rest", "/api/spec", "/api")
# Refuses every cookie, so no request carries one.
_NO_COOKIES = DefaultCookiePolicy(allowed_domains=[])


def normalize_personal_access_token(token: str) -> str:
    """Clean a pasted token: drop a copied ``Bearer`` prefix and surrounding space.

    The token is sent exactly as the wiki showed it, which already carries the
    ``pat:`` prefix the handbook describes.
    """
    token = re.sub(r"^bearer(\s+|$)", "", (token or "").strip(), flags=re.IGNORECASE).strip()
    if not token:
        raise ValueError("Drupal Wiki personal access token is required")
    return token


def _require_supported_auth_type(auth_type: str | None) -> None:
    normalized = str(auth_type or "API_TOKEN").strip().upper()
    if normalized not in SUPPORTED_AUTH_TYPES:
        raise ValueError(
            f"Unsupported Drupal Wiki auth type: {normalized}. Supported: {', '.join(SUPPORTED_AUTH_TYPES)}"
        )


def normalize_base_url(base_url: str) -> str:
    """Reduce a pasted wiki or API URL to the wiki origin, e.g. ``https://wiki.example.com``."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("Drupal Wiki base URL is required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Drupal Wiki base URL must start with http:// or https://, got {base_url!r}")
    for suffix in _API_SUFFIXES:
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# Describing a failed call
#
# The REST API answers an error with a ``ProblemResponse`` (``status``, ``title``,
# ``detail``, ``errorCode``); the internal GraphQL API with an ``errors`` array; a
# proxy or SSO page in front of the wiki with HTML. Every log line and message about
# a failed call goes through these, so each carries the wiki's own reason and none
# carries a secret or a whole body.
# ---------------------------------------------------------------------------

# A reason is a sentence, not a document: a body can echo customer content.
MAX_REASON_CHARS = 300

_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_NOISE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) > MAX_REASON_CHARS:
        text = text[:MAX_REASON_CHARS].rstrip() + "..."
    return redact_urls_in_text(text)


def problem_reason(body: object) -> str:
    """``"<errorCode>: <detail>"`` from a ProblemResponse or GraphQL error body; "" if none."""
    if not isinstance(body, dict):
        return ""
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        first = errors[0]
        extensions = first.get("extensions")
        code = extensions.get("code") or extensions.get("classification") if isinstance(extensions, dict) else None
        message = first.get("message")
    else:
        code = body.get("errorCode") or body.get("error")
        title, detail = body.get("title"), body.get("detail")
        message = detail if detail else title
        if title and detail and str(title) not in str(detail):
            message = f"{title}: {detail}"
        message = message or body.get("message")
    parts = [str(part) for part in (code, message) if part not in (None, "")]
    return _clean(": ".join(parts)) if parts else ""


def text_reason(text: object) -> str:
    """A short reason from a non-JSON body: an HTML page's title, else its leading text."""
    if not isinstance(text, str) or not text.strip():
        return ""
    title = _HTML_TITLE.search(text)
    if title and title.group(1).strip():
        return _clean(title.group(1))
    return _clean(_TAG.sub(" ", _HTML_NOISE.sub(" ", text)))


def describe_response(response: object) -> str:
    """``"<errorCode>: <detail> (HTTP <status>)"`` for a failed ``HTTPResponse``.

    Falls back to the body's text, then to the status alone. Never raises: it runs on
    the error path.
    """
    status = getattr(response, "status", None)
    reason = ""
    try:
        reason = problem_reason(response.json())  # type: ignore[attr-defined]
    except Exception:
        reason = ""
    if not reason:
        try:
            reason = text_reason(response.text())  # type: ignore[attr-defined]
        except Exception:
            reason = ""
    suffix = f"HTTP {status}" if isinstance(status, int) else "no HTTP status"
    return f"{reason} ({suffix})" if reason else suffix


def describe_exception(error: BaseException) -> str:
    """The same line for an exception: the wiki's reason when it carries a response,
    else the exception's own message, else its type (a timeout's message is empty)."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        reason = ""
        try:
            reason = problem_reason(response.json())  # type: ignore[union-attr]
        except Exception:
            reason = ""
        if not reason:
            try:
                reason = text_reason(response.text)  # type: ignore[union-attr]
            except Exception:
                reason = ""
        return f"{reason} (HTTP {status})" if reason else f"{type(error).__name__} (HTTP {status})"
    message = _clean(str(error)) if str(error).strip() else ""
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


async def check_wiki_address(base_url: str) -> None:
    """Refuse a wiki address this deployment must not call.

    The same rule as other admin-supplied hosts (``vetted_addresses``): link-local and
    cloud metadata addresses never, private ones only while
    ``PIPESHUB_BLOCK_PRIVATE_ADDRESSES`` is on, because most Drupal Wikis run on-premise
    on a private network.

    Raises:
        HostCheckError: a ``ValueError`` whose message is written for the admin.
    """
    host = urlparse(base_url).hostname
    if not host:
        raise ValueError(f"Drupal Wiki base URL has no host: {base_url!r}")
    _ = await vetted_addresses(host)


class DrupalWikiRESTClientViaToken(HTTPClient):
    """Drupal Wiki REST client authenticated with a personal access token.

    Sends ``Authorization: Bearer <token>`` plus the API version and vendor media
    types on every request. The token acts with its owner's rights, so what the
    client can read is exactly what that user can read.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        resilience: ResiliencePolicy | None = None,
    ) -> None:
        normalized_token = normalize_personal_access_token(token)
        super().__init__(normalized_token, "Bearer", timeout=timeout, resilience=resilience)
        self.base_url = normalize_base_url(base_url)
        self.token = normalized_token
        self.headers.update({
            "Accept": ACCEPT_HEADER,
            "X-API-Version": API_VERSION,
        })

    async def _ensure_client(self) -> httpx.AsyncClient:
        client = await super()._ensure_client()
        # Drupal Wiki answers token calls with a SESSION cookie, then rejects a call
        # that carries that cookie and the token together (HTTP 400, "trouble
        # authenticating"), so every call after the first would fail.
        client.cookies.jar.set_policy(_NO_COOKIES)
        # check_wiki_address vets the wiki's own host at init, but a redirect can point
        # anywhere and downloads follow redirects, so every hop to another host is vetted.
        if self._vet_redirect not in client.event_hooks["request"]:
            client.event_hooks["request"].append(self._vet_redirect)
        return client

    async def _vet_redirect(self, request: httpx.Request) -> None:
        """Refuse a request to a host other than the wiki's, unless it passes the same check.

        Raises:
            HostCheckError: the host is one this deployment must not call.
        """
        if request.url.host != urlparse(self.base_url).hostname:
            _ = await vetted_addresses(request.url.host)

    def get_base_url(self) -> str:
        return self.base_url

    def get_token(self) -> str:
        return self.token


@dataclass
class DrupalWikiTokenConfig:
    """Configuration for the Drupal Wiki REST client.

    Args:
        base_url: Wiki URL, e.g. ``https://wiki.example.com``
        token: Personal access token, exactly as the wiki shows it
        timeout: Request timeout in seconds
    """

    base_url: str
    token: str
    timeout: float = 30.0

    def create_client(self, resilience: ResiliencePolicy | None = None) -> DrupalWikiRESTClientViaToken:
        return DrupalWikiRESTClientViaToken(self.base_url, self.token, self.timeout, resilience)

    def to_dict(self) -> dict:
        return asdict(self)


class DrupalWikiClient(IClient):
    """Builder for Drupal Wiki clients."""

    def __init__(self, client: DrupalWikiRESTClientViaToken) -> None:
        self.client = client

    def get_client(self) -> DrupalWikiRESTClientViaToken:
        return self.client

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: DrupalWikiTokenConfig,
        resilience: ResiliencePolicy | None = None,
    ) -> "DrupalWikiClient":
        return cls(config.create_client(resilience))

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
        resilience: ResiliencePolicy | None = None,
    ) -> "DrupalWikiClient":
        """Build the client from ``/services/connectors/{id}/config``.

        Expects ``auth.authType`` ``API_TOKEN`` (the default when absent) with
        ``auth.baseUrl`` and ``auth.apiToken``.
        """
        try:
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)
            auth_config = config.get("auth") or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Drupal Wiki connector configuration")

            _require_supported_auth_type(auth_config.get("authType"))

            base_url = str(auth_config.get("baseUrl") or "").strip()
            api_token = str(auth_config.get("apiToken") or "").strip()
            if not base_url:
                raise ValueError("Base URL is required for Drupal Wiki API_TOKEN auth")
            if not api_token:
                raise ValueError("Personal access token is required for Drupal Wiki API_TOKEN auth")

            return cls(DrupalWikiRESTClientViaToken(base_url, api_token, resilience=resilience))
        except Exception as e:
            logger.error(f"Failed to build Drupal Wiki client from services: {e}")
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "DrupalWikiClient":
        """Build the client from a per-user toolset configuration.

        The admin sets ``baseUrl`` on the toolset instance; each user supplies their own
        ``apiToken``, so agent calls run with that user's wiki permissions.
        """
        if not toolset_config:
            raise ValueError("Toolset configuration is required")
        if not toolset_config.get("isAuthenticated", False):
            raise ValueError("Toolset is not authenticated. Please complete authentication first.")

        try:
            _require_supported_auth_type(toolset_config.get("authType"))

            instance_id = toolset_config.get("instanceId")
            if not instance_id:
                raise ValueError("instanceId is required for Drupal Wiki API_TOKEN auth")
            if not config_service:
                raise ValueError("config_service is required for Drupal Wiki API_TOKEN auth")

            # Imported here like the Jira client: edition_config pulls in the API layer.
            from app.edition_config import get_toolset_by_id

            instance = await get_toolset_by_id(instance_id, config_service, org_id=toolset_config.get("orgId"))
            if not instance:
                raise ValueError(f"Drupal Wiki instance '{instance_id}' not found")

            base_url = str((instance.get("auth") or {}).get("baseUrl") or "").strip()
            api_token = str((toolset_config.get("auth") or {}).get("apiToken") or "").strip()
            if not base_url:
                raise ValueError("Base URL is required. Admin must configure the Drupal Wiki URL.")
            if not api_token:
                raise ValueError("Personal access token is required for Drupal Wiki API_TOKEN auth")

            client = DrupalWikiRESTClientViaToken(base_url, api_token)
            # A personal action's address is its owner's: connect only to the public one checked.
            from app.agents.toolset_store import is_personal

            if instance is not None and is_personal(instance):
                client.require_public_addresses()
            logger.info("Created Drupal Wiki client from toolset config (auth type: API_TOKEN)")
            return cls(client)
        except Exception as e:
            logger.error(f"Failed to build Drupal Wiki client from toolset: {e}")
            raise ValueError(f"Failed to create Drupal Wiki client: {e}") from e

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
        except Exception as e:
            logger.error(f"Failed to get Drupal Wiki connector config: {e}")
            raise ValueError(
                f"Failed to get Drupal Wiki connector configuration for instance {connector_instance_id}"
            ) from e
        if not config:
            raise ValueError(f"Failed to get Drupal Wiki connector configuration for instance {connector_instance_id}")
        return config
