import logging
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient
from app.sources.client.resilience import ResiliencePolicy

# Drupal Wiki only offers personal access tokens: its OpenAPI spec declares a single
# bearer scheme ("PAT") and there is no OAuth or basic-auth endpoint.
SUPPORTED_AUTH_TYPES = ("API_TOKEN",)
API_VERSION = "1"
# Plain JSON only. The spec never mentions a vendor media type, and both Onyx and
# AnythingLLM read every endpoint with this, so anything more is a guess that can
# only earn a 406.
ACCEPT_HEADER = "application/json"
_API_SUFFIXES = ("/api/rest", "/api/spec", "/api")


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

            logger.info("Created Drupal Wiki client from toolset config (auth type: API_TOKEN)")
            return cls(DrupalWikiRESTClientViaToken(base_url, api_token))
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
