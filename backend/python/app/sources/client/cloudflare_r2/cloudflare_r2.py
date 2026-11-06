import logging
import re
from dataclasses import dataclass, field

from aiobotocore.session import AioSession

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient
from app.sources.client.s3.s3 import create_aio_session

R2_REGION = "auto"

_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")
_JURISDICTIONS = ("eu", "fedramp", "us")


def build_endpoint_url(account_id: str, jurisdiction: str | None = None) -> str:
    """The R2 S3 API address of an account, or a ValueError written for the admin.

    The host is always Cloudflare's: the admin supplies identifiers, not an address, so
    this setting cannot point the connector at another server.
    """
    account = (account_id or "").strip().lower()
    if not _ACCOUNT_ID.fullmatch(account):
        raise ValueError(
            "The Cloudflare account ID must be 32 hexadecimal characters. "
            "Find it on the R2 overview page of the Cloudflare dashboard."
        )
    name = (jurisdiction or "").strip().lower()
    if not name:
        return f"https://{account}.r2.cloudflarestorage.com"
    if name not in _JURISDICTIONS:
        raise ValueError(f"The R2 jurisdiction must be one of: {', '.join(_JURISDICTIONS)}, or left empty.")
    return f"https://{account}.{name}.r2.cloudflarestorage.com"


class CloudflareR2RESTClientViaAccessKey:
    """Connection to the R2 S3 API, signed with the access key pair of an R2 API token."""

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        account_id: str,
        jurisdiction: str | None = None,
    ) -> None:
        # Empty keys would make botocore fall back to whatever AWS credentials this host has.
        if not access_key_id or not secret_access_key:
            raise ValueError("The access key ID and secret access key of an R2 API token are required")
        self.endpoint_url = build_endpoint_url(account_id, jurisdiction)
        self.session = create_aio_session(access_key_id, secret_access_key, R2_REGION)
        # botocore's default adds checksum headers to GetObject and PutObject that R2's API
        # reference does not list as supported.
        self.session.set_config_variable("request_checksum_calculation", "when_required")
        self.session.set_config_variable("response_checksum_validation", "when_required")


@dataclass
class CloudflareR2AccessKeyConfig:
    access_key_id: str
    secret_access_key: str = field(repr=False)
    account_id: str
    jurisdiction: str | None = None

    def create_client(self) -> CloudflareR2RESTClientViaAccessKey:
        return CloudflareR2RESTClientViaAccessKey(
            self.access_key_id,
            self.secret_access_key,
            self.account_id,
            self.jurisdiction,
        )


class CloudflareR2Client(IClient):
    def __init__(self, client: CloudflareR2RESTClientViaAccessKey) -> None:
        self.client = client

    def get_client(self) -> CloudflareR2RESTClientViaAccessKey:
        return self.client

    def get_session(self) -> AioSession:
        return self.client.session

    def get_endpoint_url(self) -> str:
        return self.client.endpoint_url

    def get_credentials(self) -> dict[str, str]:
        """What the data source needs to connect. The keys stay inside the session."""
        return {"endpoint_url": self.client.endpoint_url}

    @classmethod
    def build_with_config(cls, config: CloudflareR2AccessKeyConfig) -> "CloudflareR2Client":
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "CloudflareR2Client":
        config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
        if not config:
            logger.error(f"No Cloudflare R2 configuration for connector {connector_instance_id}")
            raise ValueError(f"Failed to get Cloudflare R2 configuration for connector {connector_instance_id}")

        auth = config.get("auth") or {}
        return cls.build_with_config(
            CloudflareR2AccessKeyConfig(
                access_key_id=auth.get("accessKey", ""),
                secret_access_key=auth.get("secretKey", ""),
                account_id=auth.get("accountId", ""),
                jurisdiction=auth.get("jurisdiction"),
            )
        )
