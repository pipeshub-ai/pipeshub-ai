import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from aiobotocore.credentials import AioDeferredRefreshableCredentials
from aiobotocore.session import AioSession

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


def create_aio_session(
    access_key_id: str | None,
    secret_access_key: str | None,
    region_name: str | None,
) -> AioSession:
    """Build an aiobotocore session holding static keys and a region.

    Mirrors ``boto3.Session(...)``: empty keys leave the default credential
    chain in place rather than signing with blank credentials.
    """
    session = AioSession()
    if access_key_id or secret_access_key:
        session.set_credentials(access_key_id, secret_access_key)
    if region_name is not None:
        session.set_config_variable("region", region_name)
    return session


@dataclass
class S3Response:
    """Standardized S3 API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None
    # The error text is built from the source's own strings (which can carry the
    # bucket and object key), so it cannot be substring-matched to classify a
    # failure. The status is read off the botocore error instead.
    status_code: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class S3RESTClientViaAccessKey:
    """S3 REST client via Access Key and Secret Key using aiobotocore
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

    def create_session(self) -> AioSession:
        """Create an aiobotocore session using access key and secret key"""
        self.session = create_aio_session(
            self.access_key_id, self.secret_access_key, self.region_name
        )
        return self.session

    def get_session(self) -> AioSession:
        """Get the aiobotocore session"""
        if self.session is None:
            self.session = self.create_session()
        return self.session

    async def get_s3_client(self) -> object:
        """Get an S3 client context manager from the aiobotocore session"""
        session = self.get_session()
        return session.create_client('s3')

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


class S3RESTClientViaAssumeRole(S3RESTClientViaAccessKey):
    """S3 client that signs with temporary credentials from STS AssumeRole.

    The base access key is used only to call STS, never the default credential
    chain: on EKS that chain is PipesHub's own storage role.
    """

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        role_arn: str,
        session_name: str,
        region_name: str,
        external_id: Optional[str] = None,
        bucket_name: Optional[str] = None,
    ) -> None:
        super().__init__(access_key_id, secret_access_key, region_name, bucket_name)
        self.role_arn = role_arn
        self.session_name = session_name
        self.external_id = external_id

    async def _assume_role(self) -> dict[str, str]:
        kwargs = {"RoleArn": self.role_arn, "RoleSessionName": self.session_name}
        if self.external_id:
            kwargs["ExternalId"] = self.external_id
        base_session = create_aio_session(self.access_key_id, self.secret_access_key, self.region_name)
        async with base_session.create_client("sts") as sts:
            credentials = (await sts.assume_role(**kwargs))["Credentials"]
        return {
            "access_key": credentials["AccessKeyId"],
            "secret_key": credentials["SecretAccessKey"],
            "token": credentials["SessionToken"],
            "expiry_time": credentials["Expiration"].isoformat(),
        }

    def create_session(self) -> AioSession:
        """Create an aiobotocore session whose credentials re-assume the role before they expire"""
        session = create_aio_session(None, None, self.region_name)
        session._credentials = AioDeferredRefreshableCredentials(
            refresh_using=self._assume_role, method="sts-assume-role"
        )
        self.session = session
        return self.session


@dataclass
class S3AccessKeyConfig:
    """Configuration for S3 REST client via Access Key and Secret Key using aiobotocore
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
    """Builder class for S3 clients with different construction methods using aiobotocore"""

    def __init__(self, client: S3RESTClientViaAccessKey) -> None:
        """Initialize with an S3 client object"""
        self.client = client

    def get_client(self) -> S3RESTClientViaAccessKey:
        """Return the S3 client object"""
        return self.client

    async def get_s3_client(self) -> object:
        """Return the aiobotocore S3 client context manager"""
        return await self.client.get_s3_client()

    def get_session(self) -> AioSession:
        """Get the aiobotocore session"""
        return self.client.get_session()

    def get_bucket_name(self) -> Optional[str]:
        """Get the configured bucket name"""
        return self.client.get_bucket_name()

    def set_bucket_name(self, bucket_name: str) -> None:
        """Set the bucket name for operations"""
        self.client.set_bucket_name(bucket_name)

    def get_credentials(self) -> Dict[str, str]:
        """Get AWS credentials for aiobotocore session creation"""
        return self.client.get_credentials()

    def get_region_name(self) -> str:
        """Get the configured region name"""
        return self.client.get_region_name()

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
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "S3Client":
        """Build S3Client using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            org_id: Organization ID (optional)
            user_id: User ID (optional)
        Returns:
            S3Client instance
        """
        try:
            # Get S3 configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)

            if not config:
                raise ValueError("Failed to get S3 connector configuration")

            auth_config = config.get("auth", {})
            auth_type = auth_config.get("authType", "ACCESS_KEY")
            access_key_id = auth_config.get("accessKey", "")
            secret_access_key = auth_config.get("secretKey", "")
            region_name = auth_config.get("region", "us-east-1")
            bucket_name = auth_config.get("bucket")

            if auth_type not in ("ACCESS_KEY", "IAM_ROLE"):
                raise ValueError(f"Invalid auth type: {auth_type}")
            if not access_key_id or not secret_access_key:
                raise ValueError("Access key ID and secret access key are required for S3 authentication")

            if auth_type == "IAM_ROLE":
                role_arn = auth_config.get("roleArn", "")
                if not role_arn:
                    raise ValueError("Role ARN is required for S3 IAM role authentication")
                client = S3RESTClientViaAssumeRole(
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    role_arn=role_arn,
                    # STS caps RoleSessionName at 64 characters.
                    session_name=f"pipeshub-s3-{connector_instance_id}"[:64],
                    region_name=region_name,
                    external_id=auth_config.get("externalId") or None,
                    bucket_name=bucket_name,
                )
            else:
                client = S3RESTClientViaAccessKey(
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    region_name=region_name,
                    bucket_name=bucket_name
                )

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build S3 client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch connector config from etcd for S3."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get S3 connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get S3 connector config: {e}")
            raise ValueError(f"Failed to get S3 connector configuration for instance {connector_instance_id}")
