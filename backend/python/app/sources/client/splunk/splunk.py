import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

from splunklib import (
    client,  # type: ignore
    results,  # type: ignore
)

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class SplunkResponse:
    """Standardized Splunk API response wrapper"""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class SplunkRESTClientViaUsernamePassword:
    """Splunk REST client via username and password
    Args:
        host: The Splunk server hostname
        port: The Splunk management port
        username: The username to use for authentication
        password: The password to use for authentication
        scheme: The scheme to use (http or https)
    """

    def __init__(
        self,
        host: str,
        port: int = 8089,
        username: str = "",
        password: str = "",
        scheme: str = "https",
    ) -> None:
        if not host:
            raise ValueError("Splunk host cannot be empty")
        if not username or not password:
            raise ValueError("Splunk username and password are required")

        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.scheme = scheme
        self._service: client.Service | None = None

    def get_service(self) -> client.Service:
        """Get or create the Splunk service connection"""
        if self._service is None:
            self._service = client.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                scheme=self.scheme,
            )
        return self._service


class SplunkRESTClientViaToken:
    """Splunk REST client via token
    Args:
        host: The Splunk server hostname
        port: The Splunk management port
        token: The token to use for authentication
        scheme: The scheme to use (http or https)
    """

    def __init__(
        self,
        host: str,
        port: int = 8089,
        token: str = "",
        scheme: str = "https",
    ) -> None:
        if not host:
            raise ValueError("Splunk host cannot be empty")
        if not token:
            raise ValueError("Splunk token cannot be empty")

        self.host = host
        self.port = port
        self.token = token
        self.scheme = scheme
        self._service: client.Service | None = None

    def get_service(self) -> client.Service:
        """Get or create the Splunk service connection"""
        if self._service is None:
            self._service = client.connect(
                host=self.host,
                port=self.port,
                splunkToken=self.token,
                scheme=self.scheme,
            )
        return self._service


@dataclass
class SplunkUsernamePasswordConfig:
    """Configuration for Splunk REST client via username and password
    Args:
        host: The Splunk server hostname
        port: The Splunk management port
        username: The username to use for authentication
        password: The password to use for authentication
        scheme: The scheme to use (http or https)
    """

    host: str
    username: str
    password: str
    port: int = 8089
    scheme: str = "https"

    def create_client(self) -> SplunkRESTClientViaUsernamePassword:
        return SplunkRESTClientViaUsernamePassword(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            scheme=self.scheme,
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

    def get_service(self) -> client.Service:
        return self.create_client().get_service()


@dataclass
class SplunkTokenConfig:
    """Configuration for Splunk REST client via token
    Args:
        host: The Splunk server hostname
        port: The Splunk management port
        token: The token to use for authentication
        scheme: The scheme to use (http or https)
    """

    host: str
    token: str
    port: int = 8089
    scheme: str = "https"

    def create_client(self) -> SplunkRESTClientViaToken:
        return SplunkRESTClientViaToken(
            host=self.host,
            port=self.port,
            token=self.token,
            scheme=self.scheme,
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

    def get_service(self) -> client.Service:
        return self.create_client().get_service()


class SplunkClient(IClient):
    """Builder class for Splunk clients with different construction methods"""

    def __init__(
        self,
        client_obj: SplunkRESTClientViaUsernamePassword | SplunkRESTClientViaToken,
    ) -> None:
        """Initialize with a Splunk client object"""
        self.client = client_obj
        self.logger = logging.getLogger(__name__)

    def get_client(
        self,
    ) -> SplunkRESTClientViaUsernamePassword | SplunkRESTClientViaToken:
        """Return the Splunk client object"""
        return self.client

    def get_service(self) -> client.Service:
        """Return the Splunk service connection"""
        return self.client.get_service()

    def run_search(self, query: str, **kwargs: Any) -> str:
        """Run a search query and return the job ID
        Args:
            query: The Splunk search query string
            **kwargs: Additional parameters for the search job
        Returns:
            The search job ID (SID)
        """
        try:
            service = self.get_service()
            job = service.jobs.create(query, **kwargs)
            self.logger.info(f"Search job created with SID: {job.sid}")
            return job.sid
        except Exception as e:
            self.logger.error(f"Failed to create search job: {e!s}")
            raise

    def get_search_results(
        self,
        job_id: str,
        output_mode: str = "json",
    ) -> dict[str, Any] | None:
        """Retrieve search results for a given job ID
        Args:
            job_id: The search job ID (SID)
            output_mode: The output mode for results (default: json)

        Returns:
            Dictionary containing search results or None if job is not complete

        """
        try:
            service = self.get_service()
            job = service.jobs[job_id]

            if not job.is_done():
                self.logger.warning(f"Job {job_id} is not yet complete")
                return None

            results_stream = job.results(output_mode=output_mode)
            reader = results.JSONResultsReader(results_stream)

            results_list = []
            for result in reader:
                if isinstance(result, dict):
                    results_list.append(result)
                elif isinstance(result, results.Message):
                    self.logger.warning(f"Message from Splunk: {result}")

            self.logger.info(
                f"Retrieved {len(results_list)} results for job ID: {job_id}"
            )
            return {"results": results_list, "job_id": job_id}

        except Exception as e:
            self.logger.error(f"Failed to retrieve search results: {e!s}")
            raise

    @classmethod
    def build_with_config(
        cls,
        config: SplunkUsernamePasswordConfig | SplunkTokenConfig,
    ) -> "SplunkClient":
        """Build SplunkClient with configuration
        Args:
            config: SplunkConfig instance
        Returns:
            SplunkClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "SplunkClient":
        """Build SplunkClient using configuration service or environment variables
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            SplunkClient instance
        """
        try:
            # Try to get configuration from service first
            config = await cls._get_connector_config(logger, config_service)

            # If config service doesn't have config, fall back to environment variables
            if not config:
                logger.info("No config found in service, using environment variables")
                config = cls._get_config_from_env()

            if not config:
                raise ValueError("Failed to get Splunk connector configuration")

            # Extract configuration values
            auth_type = config.get("authType", "TOKEN")  # TOKEN or USERNAME_PASSWORD
            host = config.get("host", os.getenv("SPLUNK_HOST", "localhost"))
            port = int(config.get("port", os.getenv("SPLUNK_PORT", "8089")))
            scheme = config.get("scheme", os.getenv("SPLUNK_SCHEME", "https"))

            # Create appropriate client based on auth type
            if auth_type == "USERNAME_PASSWORD":
                username = config.get("username", os.getenv("SPLUNK_USERNAME", ""))
                password = config.get("password", os.getenv("SPLUNK_PASSWORD", ""))
                if not username or not password:
                    raise ValueError(
                        "Username and password required for username_password auth type",
                    )
                client_obj = SplunkRESTClientViaUsernamePassword(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    scheme=scheme,
                )

            elif auth_type == "TOKEN" or auth_type == "API_TOKEN":
                token = config.get("token", os.getenv("SPLUNK_TOKEN", ""))
                if not token:
                    raise ValueError("Token required for token auth type")
                client_obj = SplunkRESTClientViaToken(
                    host=host,
                    port=port,
                    token=token,
                    scheme=scheme,
                )

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client_obj)

        except Exception as e:
            logger.error(f"Failed to build Splunk client from services: {e!s}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Splunk."""
        try:
            config = await config_service.get_config(
                "/services/connectors/splunk/config"
            )
            if not config or not isinstance(config, dict):
                return {}
            return config.get("auth", {}) or {}
        except Exception as e:
            logger.error(f"Failed to get Splunk connector config: {e}")
            return {}

    @staticmethod
    def _get_config_from_env() -> dict[str, Any]:
        """Get configuration from environment variables"""
        config: dict[str, Any] = {}

        # Determine auth type based on available environment variables
        token = os.getenv("SPLUNK_TOKEN")
        username = os.getenv("SPLUNK_USERNAME")
        password = os.getenv("SPLUNK_PASSWORD")

        if token:
            config["authType"] = "TOKEN"
            config["token"] = token
        elif username and password:
            config["authType"] = "USERNAME_PASSWORD"
            config["username"] = username
            config["password"] = password
        else:
            return {}

        # Get connection settings
        if os.getenv("SPLUNK_HOST"):
            config["host"] = os.getenv("SPLUNK_HOST")
        if os.getenv("SPLUNK_PORT"):
            config["port"] = os.getenv("SPLUNK_PORT")
        if os.getenv("SPLUNK_SCHEME"):
            config["scheme"] = os.getenv("SPLUNK_SCHEME")

        return config
