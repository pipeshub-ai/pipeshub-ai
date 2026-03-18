import logging
from typing import Any

import splunklib.client as splunk_client  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class SplunkResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class SplunkClientViaCredentials:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        scheme: str = "https",
        app: str | None = None,
        owner: str | None = None,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.scheme = scheme
        self.app = app
        self.owner = owner

        self._sdk: splunk_client.Service | None = None  # type: ignore[no-any-unimported]

    def create_client(self) -> Any:  # splunk_client.Service
        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "scheme": self.scheme,
        }
        if self.app is not None:
            kwargs["app"] = self.app
        if self.owner is not None:
            kwargs["owner"] = self.owner

        self._sdk = splunk_client.connect(**kwargs)  # type: ignore[no-untyped-call]
        try:
            _ = self._sdk.info  # type: ignore[no-untyped-call]
        except Exception as e:
            raise RuntimeError("Splunk authentication failed") from e

        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # splunk_client.Service
        if self._sdk is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class SplunkClientViaToken:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        *,
        scheme: str = "https",
        app: str | None = None,
        owner: str | None = None,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.token = token
        self.scheme = scheme
        self.app = app
        self.owner = owner

        self._sdk: splunk_client.Service | None = None  # type: ignore[no-any-unimported]

    def create_client(self) -> Any:  # splunk_client.Service
        kwargs: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "splunkToken": self.token,
            "scheme": self.scheme,
        }
        if self.app is not None:
            kwargs["app"] = self.app
        if self.owner is not None:
            kwargs["owner"] = self.owner

        self._sdk = splunk_client.connect(**kwargs)  # type: ignore[no-untyped-call]
        try:
            _ = self._sdk.info  # type: ignore[no-untyped-call]
        except Exception as e:
            raise RuntimeError("Splunk authentication failed") from e

        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # splunk_client.Service
        if self._sdk is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class SplunkCredentialsConfig(BaseModel):
    host: str = Field(..., description="Splunk host")
    port: int = Field(default=8089, description="Splunk management port")
    username: str = Field(..., description="Splunk username")
    password: str = Field(..., description="Splunk password")
    scheme: str = Field(default="https", description="Connection scheme")
    app: str | None = Field(
        default=None, description="Splunk app context"
    )
    owner: str | None = Field(
        default=None, description="Splunk owner context"
    )

    def create_client(self) -> SplunkClientViaCredentials:
        return SplunkClientViaCredentials(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            scheme=self.scheme,
            app=self.app,
            owner=self.owner,
        )


class SplunkTokenConfig(BaseModel):
    host: str = Field(..., description="Splunk host")
    port: int = Field(default=8089, description="Splunk management port")
    token: str = Field(..., description="Splunk bearer token")
    scheme: str = Field(default="https", description="Connection scheme")
    app: str | None = Field(
        default=None, description="Splunk app context"
    )
    owner: str | None = Field(
        default=None, description="Splunk owner context"
    )

    def create_client(self) -> SplunkClientViaToken:
        return SplunkClientViaToken(
            host=self.host,
            port=self.port,
            token=self.token,
            scheme=self.scheme,
            app=self.app,
            owner=self.owner,
        )


SplunkClientWrapper = SplunkClientViaCredentials | SplunkClientViaToken


class SplunkClient(IClient):
    def __init__(self, client: SplunkClientWrapper) -> None:
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> SplunkClientWrapper:
        return self.client

    def get_sdk(self) -> Any:  # splunk_client.Service
        return self.client.get_sdk()  # type: ignore[reportUnknownMemberType]

    @classmethod
    def build_with_config(
        cls,
        config: SplunkCredentialsConfig | SplunkTokenConfig,
    ) -> "SplunkClient":
        client = config.create_client()
        _ = client.get_sdk()  # type: ignore[reportUnknownMemberType]
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "SplunkClient":
        """Build SplunkClient using configuration service."""
        config = await cls._get_connector_config(
            logger, config_service, connector_instance_id
        )
        if not config:
            raise ValueError(
                "Failed to get Splunk connector configuration"
            )
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "CREDENTIALS")
        host = auth_config.get("host", "localhost")
        port = auth_config.get("port", 8089)
        scheme = auth_config.get("scheme", "https")
        app = auth_config.get("app")
        owner = auth_config.get("owner")

        if auth_type == "CREDENTIALS":
            username = auth_config.get("username", "")
            password = auth_config.get("password", "")
            if not username or not password:
                raise ValueError(
                    "username and password required for CREDENTIALS auth"
                )
            wrapper: SplunkClientWrapper = SplunkClientViaCredentials(
                host=host,
                port=port,
                username=username,
                password=password,
                scheme=scheme,
                app=app,
                owner=owner,
            )
        elif auth_type == "BEARER_TOKEN":
            token = auth_config.get("token", "")
            if not token:
                raise ValueError("token required for BEARER_TOKEN auth")
            wrapper = SplunkClientViaToken(
                host=host,
                port=port,
                token=token,
                scheme=scheme,
                app=app,
                owner=owner,
            )
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")

        _ = wrapper.create_client()  # type: ignore[reportUnknownVariableType,reportUnknownMemberType]
        return cls(wrapper)

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Splunk."""
        try:
            config: dict[str, Any] = await config_service.get_config(  # type: ignore[assignment]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get Splunk connector configuration for instance {connector_instance_id}"
                )
            return config
        except Exception as e:
            logger.error(
                "Failed to get Splunk connector config: %s", e
            )
            raise ValueError(
                f"Failed to get Splunk connector configuration for instance {connector_instance_id}"
            ) from e
