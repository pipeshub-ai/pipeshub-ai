import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

try:
    import aioboto3  # type: ignore
except ImportError:
    raise ImportError("aioboto3 is not installed. Please install it with `pip install aioboto3`")

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class S3Response:
    """Standardized S3 API response wrapper"""
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


class S3RESTClientViaAccessKey:
    """S3 REST client via Access Key and Secret Key using aioboto3
    Args:
        access_key_id: The AWS access key ID
        secret_access_key: The AWS secret access key
        region_name: The AWS region name
        bucket_name: The S3 bucket name (optional, can be set per operation)
    """

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        region_name: str,
        bucket_name: Optional[str] = None
    ) -> None:
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region_name = region_name
        self.bucket_name = bucket_name
        self.session = None

    def create_session(self) -> aioboto3.Session:  # type: ignore[valid-type]
        """Create aioboto3 session using access key and secret key"""
        self.session = aioboto3.Session(
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region_name
        )
        return self.session

    def get_session(self) -> aioboto3.Session:  # type: ignore[valid-type]
        """Get the aioboto3 session"""
        if self.session is None:
            self.session = self.create_session()
        return self.session

    async def get_s3_client(self) -> object:
        """Get an S3 client context manager from aioboto3 session"""
        session = self.get_session()
        return session.client('s3')  # type: ignore[valid-type]

    def get_bucket_name(self) -> Optional[str]:
        """Get the configured bucket name"""
        return self.bucket_name

    def set_bucket_name(self, bucket_name: str) -> None:
        """Set the bucket name for operations"""
        self.bucket_name = bucket_name

    def get_region_name(self) -> str:
        """Get the configured region name"""
        return self.region_name

    def get_credentials(self) -> Dict[str, str]:
        """Get AWS credentials"""
        return {
            'aws_access_key_id': self.access_key_id,
            'aws_secret_access_key': self.secret_access_key,
            'region_name': self.region_name
        }


@dataclass
class S3AccessKeyConfig:
    """Configuration for S3 REST client via Access Key and Secret Key using aioboto3
    Args:
        access_key_id: The AWS access key ID
        secret_access_key: The AWS secret access key
        region_name: The AWS region name
        bucket_name: The S3 bucket name (optional)
        ssl: Whether to use SSL (always True for AWS)
    """
    access_key_id: str
    secret_access_key: str
    region_name: str
    bucket_name: Optional[str] = None
    ssl: bool = True

    def create_client(self) -> S3RESTClientViaAccessKey:
        return S3RESTClientViaAccessKey(
            self.access_key_id,
            self.secret_access_key,
            self.region_name,
            self.bucket_name
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class S3Client(IClient):
    """Builder class for S3 clients with different construction methods using aioboto3"""

    def __init__(self, client: S3RESTClientViaAccessKey) -> None:
        """Initialize with an S3 client object"""
        self.client = client

    def get_client(self) -> S3RESTClientViaAccessKey:
        """Return the S3 client object"""
        return self.client

    async def get_s3_client(self) -> object:
        """Return the aioboto3 S3 client context manager"""
        return await self.client.get_s3_client()

    def get_session(self) -> aioboto3.Session:  # type: ignore[valid-type]
        """Get the aioboto3 session"""
        return self.client.get_session()  # type: ignore[valid-type]

    def get_bucket_name(self) -> Optional[str]:
        """Get the configured bucket name"""
        return self.client.get_bucket_name()

    def set_bucket_name(self, bucket_name: str) -> None:
        """Set the bucket name for operations"""
        self.client.set_bucket_name(bucket_name)

    def get_credentials(self) -> Dict[str, str]:
        """Get AWS credentials for aioboto3 session creation"""
        return self.client.get_credentials()

    @classmethod
    def build_with_config(cls, config: S3AccessKeyConfig) -> "S3Client":
        """Build S3Client with configuration
        Args:
            config: S3AccessKeyConfig instance
        Returns:
            S3Client instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> "S3Client":
        """Build S3Client using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance (optional)
            org_id: Organization ID (optional)
            user_id: User ID (optional)
        Returns:
            S3Client instance
        """
        try:
            # Get S3 configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get S3 connector configuration")

            auth_type = config.get("authType", "ACCESS_KEY")  # ACCESS_KEY or OAUTH
            auth_config = config.get("auth", {})
            if auth_type == "ACCESS_KEY":  # Default to access key auth
                # Extract configuration values
                access_key_id = auth_config.get("accessKey", "")
                secret_access_key = auth_config.get("secretKey", "")
                region_name = auth_config.get("region", "us-east-1")
                bucket_name = auth_config.get("bucket")

                if not access_key_id or not secret_access_key:
                    raise ValueError("Access key ID and secret access key are required for S3 authentication")

                client = S3RESTClientViaAccessKey(
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    region_name=region_name,
                    bucket_name=bucket_name
                )

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build S3 client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for S3."""
        try:
            config = await config_service.get_config("/services/connectors/s3/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get S3 connector config: {e}")
            return {}
