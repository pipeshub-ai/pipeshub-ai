"""Authentication service for S3 connector"""

import logging
from typing import Any, Dict, Optional

import aioboto3
from botocore.exceptions import ClientError, NoCredentialsError

from app.connectors.core.base.token_service.token_service import BaseTokenService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.sources.s3.const.const import (
    AP_SOUTH_REGION,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_SESSION_TOKEN,
    REGION_NAME,
)


class S3TokenService(BaseTokenService):
    """Handles authentication and session management for AWS S3 using credentials."""

    def __init__(self, logger: logging.Logger, config: ConnectorConfig) -> None:
        super().__init__(logger, config)
        self.session: Optional[aioboto3.Session] = None
        self._aws_access_key_id: Optional[str] = None
        self._aws_secret_access_key: Optional[str] = None
        self._region_name: Optional[str] = None
        self._session_token: Optional[str] = None

    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """Authenticate with AWS S3 using aioboto3"""
        try:
            self.logger.info("🔐 Authenticating with AWS S3 using aioboto3")

            # Extract AWS credentials
            self._aws_access_key_id = credentials.get(AWS_ACCESS_KEY_ID)
            self._aws_secret_access_key = credentials.get(AWS_SECRET_ACCESS_KEY)
            self._region_name = credentials.get(REGION_NAME, AP_SOUTH_REGION)
            self._session_token = credentials.get(AWS_SESSION_TOKEN)  # For temporary credentials

            # Validate required credentials
            if not self._aws_access_key_id or not self._aws_secret_access_key:
                self.logger.error("❌ AWS access key ID and secret access key are required")
                return False

            # Create aioboto3 session with proper configuration
            session_kwargs = {
                "aws_access_key_id": self._aws_access_key_id,
                "aws_secret_access_key": self._aws_secret_access_key,
                "region_name": self._region_name,
            }

            if self._session_token:
                session_kwargs[AWS_SESSION_TOKEN] = self._session_token

            # Create aioboto3 session
            self.session = aioboto3.Session(**session_kwargs)

            # Test authentication by listing S3 buckets using aioboto3
            async with self.session.client('s3') as s3_client:
                try:
                    # Try to list buckets to verify credentials
                    response = await s3_client.list_buckets()
                    self.logger.info("✅ Successfully authenticated with AWS S3")
                    self.logger.info(f"📋 Found {len(response.get('Buckets', []))} buckets")
                    return True

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == 'InvalidAccessKeyId':
                        self.logger.error("❌ Invalid AWS access key ID")
                    elif error_code == 'SignatureDoesNotMatch':
                        self.logger.error("❌ Invalid AWS secret access key")
                    elif error_code == 'ExpiredTokenException':
                        self.logger.error("❌ AWS session token has expired")
                    else:
                        self.logger.error(f"❌ AWS authentication failed: {error_code}")
                    return False

        except NoCredentialsError:
            self.logger.error("❌ AWS credentials not found")
            return False
        except Exception as e:
            self.logger.error(f"❌ S3 authentication failed: {str(e)}")
            return False

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh AWS credentials (for temporary credentials)"""
        try:
            self.logger.info("🔄 Refreshing AWS credentials")

            if not self.session:
                raise Exception("No active AWS session")

            # For S3, we just return the current credentials
            return {
                "aws_access_key_id": self._aws_access_key_id,
                "aws_secret_access_key": self._aws_secret_access_key,
                "region_name": self._region_name,
                "session_token": self._session_token
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to refresh AWS credentials: {str(e)}")
            raise

    async def validate_token(self, token: str) -> bool:
        """Validate current AWS credentials"""
        try:
            if not self.session:
                return False

            # Use STS GetCallerIdentity for a lightweight validation check
            async with self.session.client('sts') as sts_client:
                await sts_client.get_caller_identity()
                return True

        except Exception as e:
            self.logger.error(f"❌ AWS credentials validation failed: {str(e)}")
            return False

    async def revoke_token(self, token: str) -> bool:
        """Revoke AWS credentials (not applicable for AWS)"""
        try:
            self.logger.info("🔄 Revoking AWS credentials")

            # AWS doesn't have a direct "revoke" mechanism for access keys
            # The credentials will naturally expire or can be deactivated in AWS IAM
            self.session = None
            self._aws_access_key_id = None
            self._aws_secret_access_key = None
            self._session_token = None

            self.logger.info("✅ AWS credentials cleared")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to revoke AWS credentials: {str(e)}")
            return False

    async def disconnect(self) -> bool:
        """Disconnect and cleanup authentication"""
        try:
            self.logger.info("🔄 Disconnecting S3 authentication service")

            # Clear session and credentials
            self.session = None
            self._aws_access_key_id = None
            self._aws_secret_access_key = None
            self._session_token = None

            self.logger.info("✅ S3 authentication service disconnected successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect S3 authentication service: {str(e)}")
            return False

    def get_service(self) -> Optional[aioboto3.Session]:
        """Get the current aioboto3 session instance"""
        return self.session

    def is_connected(self) -> bool:
        """Check if service is connected"""
        return self.session is not None and self._aws_access_key_id is not None

    def get_region_name(self) -> Optional[str]:
        """Get the AWS region name"""
        return self._region_name

    def get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API calls (not applicable for AWS)"""
        # AWS uses signature-based authentication, not headers
        return {}

    def get_credentials_dict(self) -> Dict[str, str]:
        """Get AWS credentials as a dictionary"""
        if self.is_connected():
            credentials = {
                "aws_access_key_id": self._aws_access_key_id,
                "aws_secret_access_key": self._aws_secret_access_key,
                "region_name": self._region_name,
            }
            if self._session_token:
                credentials[AWS_SESSION_TOKEN] = self._session_token
            return credentials
        return {}
