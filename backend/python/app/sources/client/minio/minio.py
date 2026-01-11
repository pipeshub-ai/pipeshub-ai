import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

try:
    import aioboto3  # type: ignore
except ImportError:
    raise ImportError("aioboto3 is not installed. Please install it with `pip install aioboto3`")

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class MinIORESTClientViaAccessKey:
    """MinIO REST client via Access Key and Secret Key using aioboto3.

    MinIO is S3-compatible, so we use aioboto3 with a custom endpoint_url.

    Args:
        access_key_id: The MinIO access key ID
        secret_access_key: The MinIO secret access key
        endpoint_url: The MinIO server endpoint (e.g., http://localhost:9000)
        bucket_name: The bucket name (optional, can be set per operation)
        use_ssl: Whether to use SSL/TLS (default: True)
        verify_ssl: Whether to verify SSL certificates (default: True)
        region_name: Region name (default: us-east-1, required by boto3 but not used by MinIO)
    """

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        endpoint_url: str,
        bucket_name: Optional[str] = None,
        use_ssl: bool = True,
        verify_ssl: bool = True,
        region_name: str = "us-east-1"
    ) -> None:
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.endpoint_url = endpoint_url
        self.bucket_name = bucket_name
        self.use_ssl = use_ssl
        self.verify_ssl = verify_ssl
        self.region_name = region_name
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
        """Get an S3 client context manager from aioboto3 session with MinIO endpoint.

        Note: SSL usage is determined by the URL scheme (http:// vs https://) in endpoint_url.
        The verify parameter controls SSL certificate verification.
        """
        session = self.get_session()
        return session.client(
            's3',
            endpoint_url=self.endpoint_url,
            verify=self.verify_ssl
        )

    def get_bucket_name(self) -> Optional[str]:
        """Get the configured bucket name"""
        return self.bucket_name

    def set_bucket_name(self, bucket_name: str) -> None:
        """Set the bucket name for operations"""
        self.bucket_name = bucket_name

    def get_region_name(self) -> str:
        """Get the configured region name"""
        return self.region_name

    def get_endpoint_url(self) -> str:
        """Get the MinIO endpoint URL"""
        return self.endpoint_url

    def get_credentials(self) -> Dict[str, Any]:
        """Get credentials and connection settings"""
        return {
            'aws_access_key_id': self.access_key_id,
            'aws_secret_access_key': self.secret_access_key,
            'region_name': self.region_name,
            'endpoint_url': self.endpoint_url,
            'use_ssl': self.use_ssl,
            'verify_ssl': self.verify_ssl
        }


@dataclass
class MinIOAccessKeyConfig:
    """Configuration for MinIO REST client via Access Key and Secret Key using aioboto3

    Args:
        access_key_id: The MinIO access key ID
        secret_access_key: The MinIO secret access key
        endpoint_url: The MinIO server endpoint (e.g., http://localhost:9000)
        bucket_name: The bucket name (optional)
        use_ssl: Whether to use SSL/TLS (default: True)
        verify_ssl: Whether to verify SSL certificates (default: True)
        region_name: Region name (default: us-east-1)
    """
    access_key_id: str
    secret_access_key: str
    endpoint_url: str
    bucket_name: Optional[str] = None
    use_ssl: bool = True
    verify_ssl: bool = True
    region_name: str = "us-east-1"

    def create_client(self) -> MinIORESTClientViaAccessKey:
        return MinIORESTClientViaAccessKey(
            self.access_key_id,
            self.secret_access_key,
            self.endpoint_url,
            self.bucket_name,
            self.use_ssl,
            self.verify_ssl,
            self.region_name
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class MinIOClient(IClient):
    """Builder class for MinIO clients with different construction methods using aioboto3"""

    def __init__(self, client: MinIORESTClientViaAccessKey) -> None:
        """Initialize with a MinIO client object"""
        self.client = client

    def get_client(self) -> MinIORESTClientViaAccessKey:
        """Return the MinIO client object"""
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

    def get_credentials(self) -> Dict[str, Any]:
        """Get credentials and connection settings"""
        return self.client.get_credentials()

    def get_region_name(self) -> str:
        """Get the configured region name"""
        return self.client.get_region_name()

    def get_endpoint_url(self) -> str:
        """Get the MinIO endpoint URL"""
        return self.client.get_endpoint_url()

    @classmethod
    def build_with_config(cls, config: MinIOAccessKeyConfig) -> "MinIOClient":
        """Build MinIOClient with configuration

        Args:
            config: MinIOAccessKeyConfig instance
        Returns:
            MinIOClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "MinIOClient":
        """Build MinIOClient using configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Connector instance ID
        Returns:
            MinIOClient instance
        """
        try:
            # Get MinIO configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)

            if not config:
                raise ValueError("Failed to get MinIO connector configuration")

            auth_config = config.get("auth", {})

            # Extract configuration values
            access_key_id = auth_config.get("accessKey", "")
            secret_access_key = auth_config.get("secretKey", "")
            endpoint_url = auth_config.get("endpointUrl", "")
            bucket_name = auth_config.get("bucket")
            use_ssl = auth_config.get("useSsl", True)
            verify_ssl = auth_config.get("verifySsl", True)
            region_name = auth_config.get("region", "us-east-1")

            if not access_key_id or not secret_access_key:
                raise ValueError("Access key ID and secret access key are required for MinIO authentication")

            if not endpoint_url:
                raise ValueError("Endpoint URL is required for MinIO")

            client = MinIORESTClientViaAccessKey(
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                endpoint_url=endpoint_url,
                bucket_name=bucket_name,
                use_ssl=use_ssl,
                verify_ssl=verify_ssl,
                region_name=region_name
            )

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build MinIO client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for MinIO."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get MinIO connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get MinIO connector config: {e}")
            raise ValueError(f"Failed to get MinIO connector configuration for instance {connector_instance_id}")
