import logging
from typing import Any

from google.cloud import bigquery  # type: ignore[import-untyped]
from google.oauth2 import service_account  # type: ignore[import-untyped]
from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class BigQueryResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class BigQueryClientViaServiceAccount:
    def __init__(
        self,
        service_account_json: dict[str, Any],
        project_id: str,
        *,
        location: str | None = None,
    ) -> None:
        super().__init__()
        self.service_account_json = service_account_json
        self.project_id = project_id
        self.location = location

        self._sdk: bigquery.Client | None = None  # type: ignore[no-any-unimported]

    def create_client(self) -> Any:  # bigquery.Client
        credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            self.service_account_json
        )
        kwargs: dict[str, Any] = {
            "credentials": credentials,
            "project": self.project_id,
        }
        if self.location is not None:
            kwargs["location"] = self.location

        self._sdk = bigquery.Client(**kwargs)  # type: ignore[no-untyped-call]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # bigquery.Client
        if self._sdk is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_project_id(self) -> str:
        return self.project_id


class BigQueryClientViaOAuth:
    def __init__(
        self,
        access_token: str,
        project_id: str,
        *,
        location: str | None = None,
    ) -> None:
        super().__init__()
        self.access_token = access_token
        self.project_id = project_id
        self.location = location

        self._sdk: bigquery.Client | None = None  # type: ignore[no-any-unimported]

    def create_client(self) -> Any:  # bigquery.Client
        credentials = Credentials(token=self.access_token)  # type: ignore[no-untyped-call]
        kwargs: dict[str, Any] = {
            "credentials": credentials,
            "project": self.project_id,
        }
        if self.location is not None:
            kwargs["location"] = self.location

        self._sdk = bigquery.Client(**kwargs)  # type: ignore[no-untyped-call]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_sdk(self) -> Any:  # bigquery.Client
        if self._sdk is None:  # type: ignore[reportUnknownMemberType]
            return self.create_client()  # type: ignore[reportUnknownVariableType]
        return self._sdk  # type: ignore[reportUnknownVariableType]

    def get_project_id(self) -> str:
        return self.project_id


class BigQueryServiceAccountConfig(BaseModel):
    service_account_json: dict[str, Any] = Field(
        ..., description="Service account JSON key"
    )
    project_id: str = Field(..., description="GCP project ID")
    location: str | None = Field(
        default=None, description="Default BigQuery location"
    )

    def create_client(self) -> BigQueryClientViaServiceAccount:
        return BigQueryClientViaServiceAccount(
            service_account_json=self.service_account_json,
            project_id=self.project_id,
            location=self.location,
        )


class BigQueryOAuthConfig(BaseModel):
    access_token: str = Field(..., description="OAuth access token")
    project_id: str = Field(..., description="GCP project ID")
    location: str | None = Field(
        default=None, description="Default BigQuery location"
    )

    def create_client(self) -> BigQueryClientViaOAuth:
        return BigQueryClientViaOAuth(
            access_token=self.access_token,
            project_id=self.project_id,
            location=self.location,
        )


BigQueryClientWrapper = BigQueryClientViaServiceAccount | BigQueryClientViaOAuth


class BigQueryClient(IClient):
    def __init__(self, client: BigQueryClientWrapper) -> None:
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> BigQueryClientWrapper:
        return self.client

    def get_sdk(self) -> Any:  # bigquery.Client
        return self.client.get_sdk()  # type: ignore[reportUnknownMemberType]

    @classmethod
    def build_with_config(
        cls,
        config: BigQueryServiceAccountConfig | BigQueryOAuthConfig,
    ) -> "BigQueryClient":
        client = config.create_client()
        _ = client.get_sdk()  # type: ignore[reportUnknownMemberType]
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "BigQueryClient":
        """Build BigQueryClient using configuration service."""
        config = await cls._get_connector_config(
            logger, config_service, connector_instance_id
        )
        if not config:
            raise ValueError(
                "Failed to get BigQuery connector configuration"
            )
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "SERVICE_ACCOUNT")
        project_id = auth_config.get("projectId", "")
        location = auth_config.get("location")

        if auth_type == "SERVICE_ACCOUNT":
            sa_json = auth_config.get("serviceAccountJson", {})
            if not sa_json:
                raise ValueError(
                    "serviceAccountJson required for SERVICE_ACCOUNT auth"
                )
            wrapper: BigQueryClientWrapper = BigQueryClientViaServiceAccount(
                service_account_json=sa_json,
                project_id=project_id,
                location=location,
            )
        elif auth_type == "OAUTH":
            access_token = auth_config.get("accessToken", "")
            if not access_token:
                raise ValueError("accessToken required for OAUTH auth")
            wrapper = BigQueryClientViaOAuth(
                access_token=access_token,
                project_id=project_id,
                location=location,
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
        """Fetch connector config from etcd for BigQuery."""
        try:
            config: dict[str, Any] = await config_service.get_config(  # type: ignore[assignment]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get BigQuery connector configuration for instance {connector_instance_id}"
                )
            return config
        except Exception as e:
            logger.error(
                "Failed to get BigQuery connector config: %s", e
            )
            raise ValueError(
                f"Failed to get BigQuery connector configuration for instance {connector_instance_id}"
            ) from e
