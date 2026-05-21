import logging
import os
from typing import Any

import gitlab
from gitlab import Gitlab
from pydantic import BaseModel, Field  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient
from app.utils.oauth_config import resolve_instance_url

# Hard ceiling (seconds) on a single ``Retry-After`` wait inside python-gitlab.
# python-gitlab's per-request rate-limit handling calls ``time.sleep(Retry-After)``
# whenever it sees a 429 with the header set and ``obey_rate_limit=True``
# (the default). On a misbehaving self-managed EE deployment that header can
# come back at several minutes per request; combined with ``max_retries=10``
# and the absence of progress logging in a ``get_all=True`` sweep, that turns
# any large sync into a multi-hour "looks hung" symptom. Capping the header
# here keeps the SDK behaviour unchanged for sensible servers and bounds the
# pathological case to a value the operator can actually observe.
_DEFAULT_MAX_RETRY_AFTER_SECONDS = 60
_GITLAB_MAX_RETRY_AFTER_ENV = "GITLAB_MAX_RETRY_AFTER_SECONDS"


def _resolve_max_retry_after_seconds(logger: logging.Logger | None = None) -> int:
    raw = os.environ.get(_GITLAB_MAX_RETRY_AFTER_ENV)
    if not raw:
        return _DEFAULT_MAX_RETRY_AFTER_SECONDS
    try:
        v = int(raw)
        if v <= 0:
            raise ValueError("must be > 0")
        return v
    except ValueError:
        if logger is not None:
            logger.warning(
                "Ignoring invalid %s=%r; using default %ss",
                _GITLAB_MAX_RETRY_AFTER_ENV,
                raw,
                _DEFAULT_MAX_RETRY_AFTER_SECONDS,
            )
        return _DEFAULT_MAX_RETRY_AFTER_SECONDS


class GitLabResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:  # type: ignore
        return self.model_dump()


class GitLabClientViaToken:
    def __init__(
        self,
        token: str,
        url: str | None = None,
        timeout: float | None = None,
        api_version: str | None = "4",
        retry_transient_errors: bool | None = None,
        max_retries: int | None = None,
        obey_rate_limit: bool | None = None,
        auth_type: str = "OAUTH",
        max_retry_after_seconds: int | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.token = token
        self.url = url or "https://gitlab.com"
        self.timeout = timeout
        self.api_version = api_version
        self.retry_transient_errors = retry_transient_errors
        self.max_retries = max_retries
        self.obey_rate_limit = obey_rate_limit
        self.auth_type = auth_type  # "OAUTH" or "API_TOKEN"
        self._logger = logger or logging.getLogger(__name__)
        self.max_retry_after_seconds = (
            max_retry_after_seconds
            if max_retry_after_seconds is not None
            else _resolve_max_retry_after_seconds(self._logger)
        )

        self._sdk: Gitlab | None = None

    def create_client(self) -> Gitlab:
        kwargs: dict[str, Any] = {"url": self.url}

        # Use private_token for PAT-based auth, oauth_token for OAuth flows
        if self.auth_type == "API_TOKEN":
            kwargs["private_token"] = self.token
        else:
            kwargs["oauth_token"] = self.token

        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        if self.api_version is not None:
            kwargs["api_version"] = self.api_version
        if self.retry_transient_errors is not None:
            kwargs["retry_transient_errors"] = self.retry_transient_errors
        # NOTE: ``max_retries`` and ``obey_rate_limit`` are deliberately NOT
        # forwarded to ``gitlab.Gitlab(**kwargs)``. They are per-request
        # kwargs on ``Gitlab.http_request()`` in python-gitlab, not
        # constructor kwargs. Older python-gitlab forwards unknown kwargs
        # to its HTTP backend (e.g. ``RequestsBackend.__init__``) which
        # raises ``TypeError: unexpected keyword argument 'max_retries'``.
        # We keep the fields on this wrapper for API stability with the
        # existing config surface but ignore them at construction time.

        self._sdk = gitlab.Gitlab(**kwargs)
        self._install_retry_after_cap(self._sdk)
        return self._sdk

    def _install_retry_after_cap(self, sdk: Gitlab) -> None:
        """Bound the ``Retry-After`` header python-gitlab will sleep on.

        python-gitlab 6.x rate-limit logic reads ``response.headers["Retry-After"]``
        as the wait time before retrying a 429, with no upper bound. We wrap the
        underlying ``requests.Session.send`` so that any 429 with
        ``Retry-After > max_retry_after_seconds`` is rewritten to the cap before
        the SDK sees it. This keeps python-gitlab's existing retry/backoff
        contract intact (still honours rate-limit signalling, still bounded by
        ``max_retries``) while preventing single-call hangs of multiple minutes
        on a misbehaving self-managed EE deployment.
        """
        cap = self.max_retry_after_seconds
        logger = self._logger
        session = getattr(sdk, "session", None)
        if session is None or not hasattr(session, "send"):
            return
        # Idempotency marker lives on the session, with an ``is True``
        # check so a ``MagicMock`` test session (whose auto-created
        # attributes are truthy MagicMock instances, not ``True``) does
        # not falsely report "already wrapped" and skip installation.
        if getattr(session, "_pipeshub_retry_after_capped", False) is True:
            return
        orig_send = session.send

        def capped_send(request: Any, **kwargs: Any) -> Any:
            response = orig_send(request, **kwargs)
            try:
                if response.status_code != 429:
                    return response
                raw = response.headers.get("Retry-After")
                if raw is None:
                    return response
                try:
                    seconds = int(raw)
                except (TypeError, ValueError):
                    return response
                if seconds > cap:
                    logger.warning(
                        "GitLab returned Retry-After=%ss on %s %s; capping to %ss "
                        "to avoid a long synchronous sleep inside python-gitlab.",
                        seconds,
                        getattr(request, "method", "?"),
                        getattr(request, "url", "?"),
                        cap,
                    )
                    response.headers["Retry-After"] = str(cap)
            except Exception:
                # Never let an introspection bug break a real request.
                pass
            return response

        session.send = capped_send  # type: ignore[assignment]
        try:
            session._pipeshub_retry_after_capped = True  # type: ignore[attr-defined]
        except Exception:
            # Some session-like objects may forbid arbitrary attributes;
            # in that case we just accept that re-installation re-wraps.
            pass

    def get_sdk(self) -> Gitlab:
        if self._sdk is None:
            # lazy init if not yet created
            return self.create_client()
        return self._sdk

    def get_base_url(self) -> str:
        return self.url

    def get_token(self) -> str:
        return self.token

    def set_token(self, token: str) -> None:
        """Update the active token in-place so long-lived instances pick up refreshed credentials.

        Mutates both the local ``token`` field and the underlying SDK instance's
        token attribute so API calls issued after this call use the new token
        without requiring a full client rebuild.
        """
        self.token = token
        if self._sdk is not None:
            if self.auth_type == "API_TOKEN":
                self._sdk.private_token = token
                # python-gitlab stores private token in http session headers
                self._sdk._set_auth_info()
            else:
                self._sdk.oauth_token = token
                self._sdk._set_auth_info()


class GitLabConfig(BaseModel):
    token: str = Field(..., description="GitLab private token")
    url: str | None = Field(
        default="https://gitlab.com", description="GitLab instance URL"
    )
    timeout: float | None = None
    api_version: str | None = Field(default="4", description="GitLab API version")
    retry_transient_errors: bool | None = None
    max_retries: int | None = None
    obey_rate_limit: bool | None = None

    def create_client(self) -> GitLabClientViaToken:
        return GitLabClientViaToken(
            token=self.token,
            url=self.url,
            timeout=self.timeout,
            api_version=self.api_version,
            retry_transient_errors=self.retry_transient_errors,
            max_retries=self.max_retries,
            obey_rate_limit=self.obey_rate_limit,
        )


class GitLabClient(IClient):
    def __init__(self, client: GitLabClientViaToken) -> None:
        self.client = client

    def get_client(self) -> GitLabClientViaToken:
        return self.client

    def get_sdk(self) -> Gitlab:
        return self.client.get_sdk()

    def get_token(self) -> str:
        return self.client.get_token()

    @classmethod
    def build_with_config(
        cls,
        config: GitLabConfig,
    ) -> "GitLabClient":
        client = config.create_client()
        client.get_sdk()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "GitLabClient":
        """Build GitLabClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            GitLabClient instance
        """
        config = await cls._get_connector_config(
            logger, config_service, connector_instance_id
        )
        if not config:
            raise ValueError("Failed to get GitLab connector configuration")
        auth_config = config.get("auth", {})
        if not auth_config:
            raise ValueError("Auth configuration missing for GitLab connector")
        credentials_config = config.get("credentials", {})
        if not credentials_config:
            raise ValueError(
                "Credentials configuration not found in Gitlab connector configuration"
            )
        auth_type = auth_config.get(
            "authType", "OAUTH"
        )  # "OAUTH" or "API_TOKEN"; default is OAUTH

        # instanceUrl supports self-managed GitLab EE; falls back to gitlab.com.
        # Resolve via shared OAuth-app config when the per-instance value is
        # missing so legacy installs (where instanceUrl was stripped from the
        # connector-instance auth) keep working.
        instance_url = await resolve_instance_url(
            auth_config,
            config_service,
            default="https://gitlab.com",
            logger=logger,
        )
        timeout = auth_config.get("timeout", 30)

        # Let python-gitlab retry transient 5xx (500/502/503/504/52x) with
        # exponential backoff. Without this, every transient GitLab blip
        # surfaces as a hard ``success=False`` to callers, which in sync
        # paths means silent data loss for that run.
        #
        # NOTE: only ``retry_transient_errors`` is accepted by python-gitlab's
        # ``Gitlab()`` constructor. ``max_retries`` and ``obey_rate_limit``
        # are per-request kwargs on ``http_request()`` and must NOT be passed
        # here — older versions forward unknown kwargs to the HTTP backend
        # which rejects them with TypeError. python-gitlab's internal
        # defaults (max_retries=10, obey_rate_limit=True per-request) are
        # what we want anyway.
        if auth_type == "API_TOKEN":
            token = auth_config.get("token", "")
            if not token:
                raise ValueError("Token required for API_TOKEN auth type")
            client = GitLabClientViaToken(
                token,
                instance_url,
                timeout,
                retry_transient_errors=True,
                auth_type="API_TOKEN",
                logger=logger,
            )
            client.create_client()
        elif auth_type == "OAUTH":
            access_token = credentials_config.get("access_token", "")
            if not access_token:
                raise ValueError("Access token required for OAuth auth type")
            client = GitLabClientViaToken(
                access_token,
                instance_url,
                timeout,
                retry_transient_errors=True,
                auth_type="OAUTH",
                logger=logger,
            )
            client.create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for GitLab."""
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get GitLab connector configuration for instance {connector_instance_id}"
                )
            return config
        except Exception as e:
            raise ValueError(
                f"Failed to get GitLab connector configuration for instance {connector_instance_id}"
            ) from e
