"""
Google Cloud Storage Client

Provides client classes for interacting with Google Cloud Storage using
service account authentication.
"""

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

try:
    from google.cloud import storage  # type: ignore
    from google.oauth2 import service_account  # type: ignore
except ImportError:
    raise ImportError(
        "google-cloud-storage is not installed. Please install it with "
        "`pip install google-cloud-storage`"
    )

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class GCSResponse:
    """Standardized GCS API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class GCSRESTClientViaServiceAccount:
    """GCS REST client via Service Account credentials.

    Args:
        service_account_info: The service account JSON key as a dictionary
        project_id: The GCP project ID (optional, derived from service account if not provided)
        bucket_name: The GCS bucket name (optional, can be set per operation)
    """

    def __init__(
        self,
        service_account_info: Dict[str, Any],
        project_id: Optional[str] = None,
        bucket_name: Optional[str] = None
    ) -> None:
        self.service_account_info = service_account_info
        self.project_id = project_id or service_account_info.get("project_id")
        self.bucket_name = bucket_name
        self._client: Optional[storage.Client] = None
        self._credentials: Optional[service_account.Credentials] = None

    def _get_credentials(self) -> service_account.Credentials:
        """Get or create service account credentials."""
        if self._credentials is None:
            self._credentials = service_account.Credentials.from_service_account_info(
                self.service_account_info
            )
        return self._credentials

    def get_client(self) -> storage.Client:
        """Get or create the GCS storage client."""
        if self._client is None:
            credentials = self._get_credentials()
            self._client = storage.Client(
                project=self.project_id,
                credentials=credentials
            )
        return self._client

    def get_bucket_name(self) -> Optional[str]:
        """Get the configured bucket name"""
        return self.bucket_name

    def set_bucket_name(self, bucket_name: str) -> None:
        """Set the bucket name for operations"""
        self.bucket_name = bucket_name

    def get_project_id(self) -> Optional[str]:
        """Get the GCP project ID"""
        return self.project_id

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get service account info (without sensitive private key details)"""
        # Return safe subset of service account info
        safe_keys = ["project_id", "client_email", "client_id", "type"]
        return {k: v for k, v in self.service_account_info.items() if k in safe_keys}


@dataclass
class GCSServiceAccountConfig:
    """Configuration for GCS REST client via Service Account credentials.

    Args:
        service_account_info: The service account JSON key as a dictionary
        project_id: The GCP project ID (optional)
        bucket_name: The GCS bucket name (optional)
    """
    service_account_info: Dict[str, Any]
    project_id: Optional[str] = None
    bucket_name: Optional[str] = None

    def create_client(self) -> GCSRESTClientViaServiceAccount:
        return GCSRESTClientViaServiceAccount(
            self.service_account_info,
            self.project_id,
            self.bucket_name
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary (excluding sensitive data)"""
        return {
            "project_id": self.project_id,
            "bucket_name": self.bucket_name,
            "client_email": self.service_account_info.get("client_email"),
        }


class GCSClient(IClient):
    """Builder class for GCS clients with different construction methods."""

    def __init__(self, client: GCSRESTClientViaServiceAccount) -> None:
        """Initialize with a GCS client object"""
        self.client = client

    def get_client(self) -> GCSRESTClientViaServiceAccount:
        """Return the GCS client object"""
        return self.client

    def get_storage_client(self) -> storage.Client:
        """Return the underlying google-cloud-storage Client"""
        return self.client.get_client()

    def get_bucket_name(self) -> Optional[str]:
        """Get the configured bucket name"""
        return self.client.get_bucket_name()

    def set_bucket_name(self, bucket_name: str) -> None:
        """Set the bucket name for operations"""
        self.client.set_bucket_name(bucket_name)

    def get_project_id(self) -> Optional[str]:
        """Get the GCP project ID"""
        return self.client.get_project_id()

    def get_credentials_info(self) -> Dict[str, Any]:
        """Get service account info (safe subset)"""
        return self.client.get_credentials_info()

    @classmethod
    def build_with_config(cls, config: GCSServiceAccountConfig) -> "GCSClient":
        """Build GCSClient with configuration

        Args:
            config: GCSServiceAccountConfig instance
        Returns:
            GCSClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "GCSClient":
        """Build GCSClient using configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Connector instance ID
        Returns:
            GCSClient instance
        """
        try:
            # Get GCS configuration from the configuration service
            config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )

            if not config:
                raise ValueError("Failed to get GCS connector configuration")

            auth_config = config.get("auth", {})

            # Extract service account JSON
            # It can be provided as a JSON string or as a dictionary
            service_account_json = auth_config.get("serviceAccountJson")

            if not service_account_json:
                raise ValueError(
                    "Service account JSON is required for GCS authentication"
                )

            # Parse JSON string if needed
            if isinstance(service_account_json, str):
                try:
                    service_account_info = json.loads(service_account_json)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Invalid service account JSON format: {e}"
                    )
            else:
                service_account_info = service_account_json

            # Validate required fields
            required_fields = ["type", "project_id", "private_key", "client_email"]
            missing_fields = [
                f for f in required_fields
                if f not in service_account_info
            ]
            if missing_fields:
                raise ValueError(
                    f"Service account JSON missing required fields: {missing_fields}"
                )

            # Extract optional fields
            project_id = auth_config.get("projectId") or service_account_info.get("project_id")
            bucket_name = auth_config.get("bucket")

            client = GCSRESTClientViaServiceAccount(
                service_account_info=service_account_info,
                project_id=project_id,
                bucket_name=bucket_name
            )

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build GCS client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for GCS."""
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get GCS connector configuration for instance "
                    f"{connector_instance_id}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get GCS connector config: {e}")
            raise ValueError(
                f"Failed to get GCS connector configuration for instance "
                f"{connector_instance_id}"
            )
