import logging
from typing import Any

from elasticsearch import Elasticsearch  # type: ignore[reportMissingImports]
from pydantic import BaseModel, Field
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class ElasticsearchResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ElasticsearchClientViaApiKey:
    def __init__(
        self,
        hosts: list[str],
        api_key_id: str,
        api_key_secret: str,
        *,
        verify_certs: bool = True,
        ca_certs: str | None = None,
        request_timeout: float | None = None,
    ) -> None:
        super().__init__()
        self.hosts = hosts
        self.api_key_id = api_key_id
        self.api_key_secret = api_key_secret
        self.verify_certs = verify_certs
        self.ca_certs = ca_certs
        self.request_timeout = request_timeout

        self._sdk: Elasticsearch | None = None

    def create_client(self) -> Any:  # Elasticsearch
        kwargs: dict[str, Any] = {
            "hosts": self.hosts,
            "api_key": (self.api_key_id, self.api_key_secret),
            "verify_certs": self.verify_certs,
        }
        if self.ca_certs is not None:
            kwargs["ca_certs"] = self.ca_certs
        if self.request_timeout is not None:
            kwargs["request_timeout"] = self.request_timeout

        self._sdk = Elasticsearch(**kwargs)  # type: ignore[no-untyped-call]
        try:
            self._sdk.info()  # type: ignore[no-untyped-call]
        except Exception as e:
            raise RuntimeError("Elasticsearch authentication failed") from e

        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # Elasticsearch
        if self._sdk is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_base_url(self) -> str:
        return self.hosts[0] if self.hosts else ""


class ElasticsearchClientViaBasicAuth:
    def __init__(
        self,
        hosts: list[str],
        username: str,
        password: str,
        *,
        verify_certs: bool = True,
        ca_certs: str | None = None,
        request_timeout: float | None = None,
    ) -> None:
        super().__init__()
        self.hosts = hosts
        self.username = username
        self.password = password
        self.verify_certs = verify_certs
        self.ca_certs = ca_certs
        self.request_timeout = request_timeout

        self._sdk: Elasticsearch | None = None

    def create_client(self) -> Any:  # Elasticsearch
        kwargs: dict[str, Any] = {
            "hosts": self.hosts,
            "basic_auth": (self.username, self.password),
            "verify_certs": self.verify_certs,
        }
        if self.ca_certs is not None:
            kwargs["ca_certs"] = self.ca_certs
        if self.request_timeout is not None:
            kwargs["request_timeout"] = self.request_timeout

        self._sdk = Elasticsearch(**kwargs)  # type: ignore[no-untyped-call]
        try:
            self._sdk.info()  # type: ignore[no-untyped-call]
        except Exception as e:
            raise RuntimeError("Elasticsearch authentication failed") from e

        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # Elasticsearch
        if self._sdk is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_base_url(self) -> str:
        return self.hosts[0] if self.hosts else ""


class ElasticsearchClientViaToken:
    def __init__(
        self,
        hosts: list[str],
        token: str,
        *,
        verify_certs: bool = True,
        ca_certs: str | None = None,
        request_timeout: float | None = None,
    ) -> None:
        super().__init__()
        self.hosts = hosts
        self.token = token
        self.verify_certs = verify_certs
        self.ca_certs = ca_certs
        self.request_timeout = request_timeout

        self._sdk: Elasticsearch | None = None

    def create_client(self) -> Any:  # Elasticsearch
        kwargs: dict[str, Any] = {
            "hosts": self.hosts,
            "bearer_auth": self.token,
            "verify_certs": self.verify_certs,
        }
        if self.ca_certs is not None:
            kwargs["ca_certs"] = self.ca_certs
        if self.request_timeout is not None:
            kwargs["request_timeout"] = self.request_timeout

        self._sdk = Elasticsearch(**kwargs)  # type: ignore[no-untyped-call]
        try:
            self._sdk.info()  # type: ignore[no-untyped-call]
        except Exception as e:
            raise RuntimeError("Elasticsearch authentication failed") from e

        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # Elasticsearch
        if self._sdk is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_base_url(self) -> str:
        return self.hosts[0] if self.hosts else ""


class ElasticsearchApiKeyConfig(BaseModel):
    hosts: list[str] = Field(..., description="Elasticsearch host URLs")
    api_key_id: str = Field(..., description="API key ID")
    api_key_secret: str = Field(..., description="API key secret")
    verify_certs: bool = Field(default=True, description="Verify TLS certificates")
    ca_certs: str | None = Field(
        default=None, description="Path to CA certificate bundle"
    )
    request_timeout: float | None = None

    def create_client(
        self,
    ) -> ElasticsearchClientViaApiKey:
        return ElasticsearchClientViaApiKey(
            hosts=self.hosts,
            api_key_id=self.api_key_id,
            api_key_secret=self.api_key_secret,
            verify_certs=self.verify_certs,
            ca_certs=self.ca_certs,
            request_timeout=self.request_timeout,
        )


class ElasticsearchBasicAuthConfig(BaseModel):
    hosts: list[str] = Field(..., description="Elasticsearch host URLs")
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")
    verify_certs: bool = Field(default=True, description="Verify TLS certificates")
    ca_certs: str | None = Field(
        default=None, description="Path to CA certificate bundle"
    )
    request_timeout: float | None = None

    def create_client(
        self,
    ) -> ElasticsearchClientViaBasicAuth:
        return ElasticsearchClientViaBasicAuth(
            hosts=self.hosts,
            username=self.username,
            password=self.password,
            verify_certs=self.verify_certs,
            ca_certs=self.ca_certs,
            request_timeout=self.request_timeout,
        )


class ElasticsearchTokenConfig(BaseModel):
    hosts: list[str] = Field(..., description="Elasticsearch host URLs")
    token: str = Field(..., description="Bearer token")
    verify_certs: bool = Field(default=True, description="Verify TLS certificates")
    ca_certs: str | None = Field(
        default=None, description="Path to CA certificate bundle"
    )
    request_timeout: float | None = None

    def create_client(
        self,
    ) -> ElasticsearchClientViaToken:
        return ElasticsearchClientViaToken(
            hosts=self.hosts,
            token=self.token,
            verify_certs=self.verify_certs,
            ca_certs=self.ca_certs,
            request_timeout=self.request_timeout,
        )


# Union type for all client wrapper flavors
ElasticsearchClientWrapper = (
    ElasticsearchClientViaApiKey
    | ElasticsearchClientViaBasicAuth
    | ElasticsearchClientViaToken
)


class ElasticsearchClient(IClient):
    def __init__(self, client: ElasticsearchClientWrapper) -> None:
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> ElasticsearchClientWrapper:
        return self.client

    def get_sdk(self) -> Any:  # Elasticsearch
        return self.client.get_sdk()  # type: ignore[reportUnknownMemberType]

    @classmethod
    def build_with_config(
        cls,
        config: ElasticsearchApiKeyConfig
        | ElasticsearchBasicAuthConfig
        | ElasticsearchTokenConfig,
    ) -> "ElasticsearchClient":
        client = config.create_client()
        _ = client.get_sdk()  # type: ignore[reportUnknownMemberType]
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "ElasticsearchClient":
        """Build ElasticsearchClient using configuration service."""
        config = await cls._get_connector_config(
            logger, config_service, connector_instance_id
        )
        if not config:
            raise ValueError(
                "Failed to get Elasticsearch connector configuration"
            )
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "API_KEY")
        hosts = auth_config.get("hosts", [])
        verify_certs = auth_config.get("verifyCerts", True)
        ca_certs = auth_config.get("caCerts")
        request_timeout = auth_config.get("requestTimeout")

        if auth_type == "API_KEY":
            api_key_id = auth_config.get("apiKeyId", "")
            api_key_secret = auth_config.get("apiKeySecret", "")
            if not api_key_id or not api_key_secret:
                raise ValueError(
                    "apiKeyId and apiKeySecret required for API_KEY auth"
                )
            wrapper: ElasticsearchClientWrapper = ElasticsearchClientViaApiKey(
                hosts=hosts,
                api_key_id=api_key_id,
                api_key_secret=api_key_secret,
                verify_certs=verify_certs,
                ca_certs=ca_certs,
                request_timeout=request_timeout,
            )
        elif auth_type == "BASIC_AUTH":
            username = auth_config.get("username", "")
            password = auth_config.get("password", "")
            if not username or not password:
                raise ValueError(
                    "username and password required for BASIC_AUTH"
                )
            wrapper = ElasticsearchClientViaBasicAuth(
                hosts=hosts,
                username=username,
                password=password,
                verify_certs=verify_certs,
                ca_certs=ca_certs,
                request_timeout=request_timeout,
            )
        elif auth_type == "BEARER_TOKEN":
            token = auth_config.get("token", "")
            if not token:
                raise ValueError("token required for BEARER_TOKEN auth")
            wrapper = ElasticsearchClientViaToken(
                hosts=hosts,
                token=token,
                verify_certs=verify_certs,
                ca_certs=ca_certs,
                request_timeout=request_timeout,
            )
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")

        _ = wrapper.create_client()
        return cls(wrapper)

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Elasticsearch."""
        try:
            config: dict[str, Any] = await config_service.get_config(  # type: ignore[assignment]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get Elasticsearch connector configuration for instance {connector_instance_id}"
                )
            return config
        except Exception as e:
            logger.error(
                "Failed to get Elasticsearch connector config: %s", e
            )
            raise ValueError(
                f"Failed to get Elasticsearch connector configuration for instance {connector_instance_id}"
            ) from e
