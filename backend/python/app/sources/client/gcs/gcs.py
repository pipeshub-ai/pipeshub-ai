import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import google.auth.exceptions  # type: ignore
from google.cloud import storage  # type: ignore
from google.cloud.exceptions import NotFound  # type: ignore
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

try:
    # Official GCS client
    pass
except ImportError:
    raise ImportError(
        "google-cloud-storage is not installed. Please install with `pip install google-cloud-storage`."
    )


class GCSConfigurationError(Exception):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class GCSBucketError(Exception):
    def __init__(self, message: str, bucket_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.bucket_name = bucket_name
        self.details = details or {}


class GCSResponse(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def to_json(self) -> str:
        return self.model_dump_json()


# --- Configuration Models ---

class BaseGCSConfig(BaseModel, ABC):
    """Base configuration class for shared fields and logic."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='ignore')

    bucketName: str
    projectId: Optional[str] = None

    @abstractmethod
    def create_storage_client(self) -> storage.Client:
        """Create and return an authenticated storage client."""
        pass

    @abstractmethod
    def get_authentication_method(self) -> str:
        """Return the name of the authentication method."""
        pass

    def to_dict(self) -> dict:
        """Standardized dictionary representation of the config."""
        return {
            "authentication_method": self.get_authentication_method(),
            "project_id": self.projectId,
            "bucket_name": self.bucketName
        }


class GCSServiceAccountJsonConfig(BaseGCSConfig):
    serviceAccountJson: str

    @field_validator('serviceAccountJson')
    @classmethod
    def validate_sa_json(cls, v: str) -> str:
        try:
            json.loads(v)
            return v
        except json.JSONDecodeError as e:
            raise ValueError('serviceAccountJson is not valid JSON') from e

    def _get_client_from_info(self) -> storage.Client:
        try:
            info = json.loads(self.serviceAccountJson)
            return storage.Client.from_service_account_info(info, project=self.projectId)
        except (json.JSONDecodeError, ValueError) as e:
            raise GCSConfigurationError("Invalid serviceAccountJson provided", {"error": str(e)}) from e

    def create_storage_client(self) -> storage.Client:
        try:
            return self._get_client_from_info()
        except Exception as e:
            raise GCSConfigurationError(f"Failed to create Storage client: {e}") from e

    def get_authentication_method(self) -> str:
        return "service_account_json"


class GCSServiceAccountFileConfig(BaseGCSConfig):
    serviceAccountFile: str

    @field_validator('serviceAccountFile')
    @classmethod
    def validate_sa_file(cls, v: str) -> str:
        if not os.path.exists(v):
            raise ValueError(f'serviceAccountFile not found at path: {v}')
        return v

    def create_storage_client(self) -> storage.Client:
        try:
            return storage.Client.from_service_account_json(self.serviceAccountFile, project=self.projectId)
        except (FileNotFoundError, google.auth.exceptions.GoogleAuthError, ValueError) as e:
            raise GCSConfigurationError("Invalid serviceAccountFile provided", {"error": str(e)}) from e
        except Exception as e:
            raise GCSConfigurationError(f"Failed to create Storage client: {e}") from e

    def get_authentication_method(self) -> str:
        return "service_account_file"


class GCSADCConfig(BaseGCSConfig):
    def create_storage_client(self) -> storage.Client:
        try:
            return storage.Client(project=self.projectId)
        except google.auth.exceptions.GoogleAuthError as e:
            raise GCSConfigurationError(f"Failed to create Storage client via ADC (Auth Error): {e}") from e
        except Exception as e:
            raise GCSConfigurationError(f"Failed to create Storage client via ADC: {e}") from e

    def get_authentication_method(self) -> str:
        return "adc"


# --- Core Client Logic ---

class GCSCoreClient:
    def __init__(self, config: BaseGCSConfig) -> None:
        self.config = config
        self._storage_client: Optional[storage.Client] = None

    def get_storage_client(self) -> storage.Client:
        if self._storage_client is None:
            self._storage_client = self.config.create_storage_client()
        return self._storage_client

    def get_bucket_name(self) -> str:
        return self.config.bucketName

    def get_project_id(self) -> Optional[str]:
        return self.config.projectId

    def get_authentication_method(self) -> str:
        return self.config.get_authentication_method()

    def get_credentials_info(self) -> Dict[str, Any]:
        return self.config.to_dict()

    async def ensure_bucket_exists(self) -> GCSResponse:
        """
        Checks if the configured bucket exists.
        """
        bucket_name = self.get_bucket_name()
        try:
            client = self.get_storage_client()
            bucket = client.bucket(bucket_name)

            if bucket.exists():
                return GCSResponse(success=True, data={"bucket_name": bucket_name, "action": "exists"}, message=f"Bucket \"{bucket_name}\" already exists")

            # Handle Emulator Creation logic
            if os.environ.get("STORAGE_EMULATOR_HOST"):
                try:
                    bucket.create()
                    return GCSResponse(success=True, data={"bucket_name": bucket_name, "action": "created"}, message=f"Bucket \"{bucket_name}\" created successfully (emulator)")
                except Exception:
                    # Race condition or eventual consistency in emulator
                    return GCSResponse(success=True, data={"bucket_name": bucket_name, "action": "exists"}, message=f"Bucket \"{bucket_name}\" already exists (emulator)")

            raise NotFound(f"Bucket {bucket_name} not found")

        except NotFound:
            raise GCSBucketError(
                f"Bucket '{bucket_name}' not found or inaccessible. Please ensure the bucket exists and you have permissions.",
                bucket_name=bucket_name
            )
        except Exception as e:
            raise GCSBucketError(
                f"Error checking bucket '{bucket_name}': {str(e)}",
                bucket_name=bucket_name,
                details={"error": str(e)}
            )

    async def close_async_client(self) -> None:
        self._storage_client = None


# --- Main Client Wrapper ---

class GCSClient(IClient):
    def __init__(self, client: GCSCoreClient) -> None:
        self.client = client

    def get_client(self) -> GCSCoreClient:
        return self.client

    def get_bucket_name(self) -> str:
        return self.client.get_bucket_name()

    def get_credentials_info(self) -> Dict[str, Any]:
        return self.client.get_credentials_info()

    def get_authentication_method(self) -> str:
        return self.client.get_authentication_method()

    async def ensure_bucket_exists(self) -> GCSResponse:
        return await self.client.ensure_bucket_exists()

    async def get_storage_client(self) -> storage.Client:
        return self.client.get_storage_client()

    async def close_async_client(self) -> None:
        await self.client.close_async_client()

    @classmethod
    def build_with_service_account_json_config(cls, config: GCSServiceAccountJsonConfig) -> "GCSClient":
        return cls(GCSCoreClient(config))

    @classmethod
    def build_with_service_account_file_config(cls, config: GCSServiceAccountFileConfig) -> "GCSClient":
        return cls(GCSCoreClient(config))

    @classmethod
    def build_with_adc_config(cls, config: GCSADCConfig) -> "GCSClient":
        return cls(GCSCoreClient(config))

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
    ) -> "GCSClient":
        try:
            config_data = await cls._get_connector_config(config_service, "gcs")

            auth_type = config_data.get("authType", "ADC")
            auth_config = config_data.get("auth", {})

            if auth_type == "SERVICE_ACCOUNT_JSON":
                config = GCSServiceAccountJsonConfig.model_validate(auth_config)
                return cls.build_with_service_account_json_config(config)

            if auth_type == "SERVICE_ACCOUNT_FILE":
                config = GCSServiceAccountFileConfig.model_validate(auth_config)
                return cls.build_with_service_account_file_config(config)

            if auth_type == "ADC":
                config = GCSADCConfig.model_validate(auth_config)
                return cls.build_with_adc_config(config)

            raise GCSConfigurationError(f"Unsupported authType: {auth_type}")

        except ValidationError as e:
            logger.error(f"Invalid GCS configuration provided: {e}")
            raise GCSConfigurationError("Invalid GCS configuration", details=e.errors()) from e

        except Exception as e:
            logger.error(f"Failed to build GCS client from services: {e}", exc_info=True)
            raise GCSConfigurationError(f"Failed to build GCS client: {e}") from e

    @staticmethod
    async def _get_connector_config(config_service: ConfigurationService, connector_name: str) -> Dict[str, Any]:
        try:
            config_path = f"/services/connectors/{connector_name}/config"
            config_data = await config_service.get_config(config_path)
            return config_data or {}
        except Exception as e:
            raise GCSConfigurationError(f"Failed to get {connector_name} configuration: {e}") from e
