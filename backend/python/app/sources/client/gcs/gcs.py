import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

try:
    # Async GCS client
    from gcloud.aio.storage import Storage  # type: ignore
    from google.oauth2 import service_account  # type: ignore
    from google.auth.credentials import Credentials  # type: ignore
    import aiohttp  # type: ignore
except ImportError as e:
    raise ImportError(
        "gcloud-aio-storage or google-auth is not installed. Please install with `pip install gcloud-aio-storage google-auth`."
    )

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class GCSConfigurationError(Exception):
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.details = details or {}


class GCSBucketError(Exception):
    def __init__(self, message: str, bucket_name: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.bucket_name = bucket_name
        self.details = details or {}


@dataclass
class GCSResponse:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class GCSServiceAccountJsonConfig:
    serviceAccountJson: str
    bucketName: str
    projectId: Optional[str] = None

    def _get_credentials(self) -> Credentials:
        try:
            info = json.loads(self.serviceAccountJson)
            return service_account.Credentials.from_service_account_info(info)
        except Exception as e:
            raise GCSConfigurationError("Invalid serviceAccountJson provided", {"error": str(e)})

    async def create_storage_client(self) -> Storage:
        try:
            creds = self._get_credentials()
            # Storage client does not accept a 'project' kwarg; project is passed to per-call methods
            return Storage(credentials=creds)
        except Exception as e:
            raise GCSConfigurationError(f"Failed to create Storage client: {e}")

    def get_authentication_method(self) -> str:
        return "service_account_json"

    def to_dict(self) -> dict:
        return {"authentication_method": "service_account_json", "project_id": self.projectId, "bucket_name": self.bucketName}


@dataclass
class GCSServiceAccountFileConfig:
    serviceAccountFile: str
    bucketName: str
    projectId: Optional[str] = None

    def _get_credentials(self) -> Credentials:
        try:
            return service_account.Credentials.from_service_account_file(self.serviceAccountFile)
        except Exception as e:
            raise GCSConfigurationError("Invalid serviceAccountFile provided", {"error": str(e)})

    async def create_storage_client(self) -> Storage:
        try:
            creds = self._get_credentials()
            return Storage(credentials=creds)
        except Exception as e:
            raise GCSConfigurationError(f"Failed to create Storage client: {e}")

    def get_authentication_method(self) -> str:
        return "service_account_file"

    def to_dict(self) -> dict:
        return {"authentication_method": "service_account_file", "project_id": self.projectId, "bucket_name": self.bucketName}


@dataclass
class GCSADCConfig:
    bucketName: str
    projectId: Optional[str] = None

    async def create_storage_client(self) -> Storage:
        try:
            # ADC: let library resolve credentials from environment/metadata
            return Storage()
        except Exception as e:
            raise GCSConfigurationError(f"Failed to create Storage client via ADC: {e}")

    def get_authentication_method(self) -> str:
        return "adc"

    def to_dict(self) -> dict:
        return {"authentication_method": "adc", "project_id": self.projectId, "bucket_name": self.bucketName}


class GCSRESTClient:
    def __init__(self, config: Union[GCSServiceAccountJsonConfig, GCSServiceAccountFileConfig, GCSADCConfig]) -> None:  # type: ignore
        self.config = config
        self._storage_client: Optional[Storage] = None

    async def get_storage_client(self) -> Storage:
        if self._storage_client is None:
            self._storage_client = await self.config.create_storage_client()
        return self._storage_client

    def get_bucket_name(self) -> str:
        return getattr(self.config, "bucketName")

    def get_project_id(self) -> Optional[str]:
        return getattr(self.config, "projectId", None)

    def get_authentication_method(self) -> str:
        return self.config.get_authentication_method()  # type: ignore

    def get_credentials_info(self) -> Dict[str, Any]:
        return self.config.to_dict()  # type: ignore

    async def ensure_bucket_exists(self) -> GCSResponse:
        bucket_name = self.get_bucket_name()
        try:
            client = await self.get_storage_client()
            # Existence check: attempt to list; if it 404s, report missing (creation via emulator requires external call)
            await client.list_objects(bucket_name)
            return GCSResponse(success=True, data={"bucket_name": bucket_name, "action": "exists"}, message=f"Bucket \"{bucket_name}\" already exists")
        except Exception as e:
            # If running against emulator, attempt to create bucket via JSON API
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")
            if emulator:
                project = self.get_project_id() or "demo-project"
                url = f"{emulator.rstrip('/')}/storage/v1/b?project={project}"
                payload = {"name": bucket_name}
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=payload) as resp:
                            if resp.status in (200, 201):
                                return GCSResponse(success=True, data={"bucket_name": bucket_name, "action": "created"}, message=f"Bucket \"{bucket_name}\" created successfully (emulator)")
                            # If already exists, treat as exists
                            if resp.status == 409:
                                return GCSResponse(success=True, data={"bucket_name": bucket_name, "action": "exists"}, message=f"Bucket \"{bucket_name}\" already exists (emulator)")
                            text = await resp.text()
                            raise GCSBucketError(f"Emulator bucket create failed: HTTP {resp.status} {text}", bucket_name=bucket_name)
                except Exception as ce:
                    raise GCSBucketError(f"Error creating bucket in emulator: {ce}", bucket_name=bucket_name)

            # Non-emulator path: surface guidance
            raise GCSBucketError(
                f"Bucket '{bucket_name}' not found or inaccessible. Create it or grant access.",
                bucket_name=bucket_name,
                details={"error": str(e)}
            )

    async def close_async_client(self) -> None:
        if self._storage_client is not None:
            await self._storage_client.close()
            self._storage_client = None


class GCSClient(IClient):
    def __init__(self, client: GCSRESTClient) -> None:  # type: ignore
        self.client = client

    def get_client(self) -> GCSRESTClient:
        return self.client

    def get_bucket_name(self) -> str:
        return self.client.get_bucket_name()

    def get_credentials_info(self) -> Dict[str, Any]:
        return self.client.get_credentials_info()

    def get_authentication_method(self) -> str:
        return self.client.get_authentication_method()

    async def ensure_bucket_exists(self) -> GCSResponse:
        return await self.client.ensure_bucket_exists()

    async def get_storage_client(self) -> Storage:
        return await self.client.get_storage_client()

    async def close_async_client(self) -> None:
        await self.client.close_async_client()

    @classmethod
    def build_with_service_account_json_config(cls, config: GCSServiceAccountJsonConfig) -> "GCSClient":  # type: ignore
        return cls(GCSRESTClient(config))

    @classmethod
    def build_with_service_account_file_config(cls, config: GCSServiceAccountFileConfig) -> "GCSClient":  # type: ignore
        return cls(GCSRESTClient(config))

    @classmethod
    def build_with_adc_config(cls, config: GCSADCConfig) -> "GCSClient":  # type: ignore
        return cls(GCSRESTClient(config))

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
    ) -> "GCSClient":  # type: ignore
        try:
            config_data = await cls._get_connector_config(config_service, "gcs")

            auth_type = config_data.get("authType", "ADC")
            auth_config = config_data.get("auth", {})
            bucket_name = auth_config.get("bucketName")

            if not bucket_name:
                raise GCSConfigurationError("bucketName is required in GCS configuration")

            if auth_type == "SERVICE_ACCOUNT_JSON":
                sa_json = auth_config.get("serviceAccountJson")
                project_id = auth_config.get("projectId")
                if not sa_json:
                    raise GCSConfigurationError("serviceAccountJson is required for SERVICE_ACCOUNT_JSON authType")
                config = GCSServiceAccountJsonConfig(serviceAccountJson=sa_json, bucketName=bucket_name, projectId=project_id)
                return cls.build_with_service_account_json_config(config)

            if auth_type == "SERVICE_ACCOUNT_FILE":
                sa_file = auth_config.get("serviceAccountFile")
                project_id = auth_config.get("projectId")
                if not sa_file:
                    raise GCSConfigurationError("serviceAccountFile is required for SERVICE_ACCOUNT_FILE authType")
                config = GCSServiceAccountFileConfig(serviceAccountFile=sa_file, bucketName=bucket_name, projectId=project_id)
                return cls.build_with_service_account_file_config(config)

            if auth_type == "ADC":
                project_id = auth_config.get("projectId")
                config = GCSADCConfig(bucketName=bucket_name, projectId=project_id)
                return cls.build_with_adc_config(config)

            raise GCSConfigurationError(f"Unsupported authType: {auth_type}")
        except Exception as e:
            logger.error(f"Failed to build GCS client from services: {e}")
            raise GCSConfigurationError(f"Failed to build GCS client: {e}")

    @staticmethod
    async def _get_connector_config(config_service: ConfigurationService, connector_name: str) -> Dict[str, Any]:
        try:
            config_path = f"/services/connectors/{connector_name}/config"
            config_data = await config_service.get_config(config_path)
            return config_data or {}
        except Exception as e:
            raise GCSConfigurationError(f"Failed to get {connector_name} configuration: {e}")


