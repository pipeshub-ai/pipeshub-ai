import logging
from dataclasses import asdict, dataclass
from typing import List, Union

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import storage
from google.cloud.storage.blob import Blob
from google.cloud.storage.bucket import Bucket

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# Set up a logger for this module
logger = logging.getLogger(__name__)

# --- 1. The Real SDK Client Wrapper ---
# This class directly wraps the official SDK, similar to SlackRESTClientViaToken
class GoogleCloudRESTClientViaServiceAccount:
    """
    This is the core client that wraps the official Google Cloud Storage SDK.
    It is initialized using a service account JSON file.
    """

    def __init__(self, service_account_json_path: str) -> None:
        try:
            self.client = storage.Client.from_service_account_json(
                service_account_json_path
            )
            logger.info("Google Cloud Storage client initialized successfully.")
        except FileNotFoundError:
            logger.error(
                f"Service account JSON file not found at: {service_account_json_path}"
            )
            raise
        except Exception as e:
            logger.error(
                f"Failed to initialize Google Cloud Storage client: {e}", exc_info=True
            )
            raise

    def get_storage_client(self) -> storage.Client:
        """
        Returns the raw, authenticated google-cloud-storage client object.
        """
        return self.client

    def list_buckets(self) -> List[Bucket]:
        """
        Lists all buckets the service account has access to.
        """
        try:
            buckets = self.client.list_buckets()
            return list(buckets)  # Convert iterator to a list
        except GoogleAPICallError as e:
            logger.error(f"GCP API error while listing buckets: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error while listing buckets: {e}", exc_info=True)
            raise

    def list_blobs(self, bucket_name: str) -> List[Blob]:
        """
        Lists all files (blobs) in a specific GCS bucket.
        :param bucket_name: The name of the GCS bucket.
        """
        try:
            bucket = self.client.bucket(bucket_name)
            blobs = bucket.list_blobs()
            return list(blobs)  # Convert iterator to a list
        except GoogleAPICallError as e:
            logger.error(
                f"GCP API error while listing blobs for bucket {bucket_name}: {e}",
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error listing blobs for {bucket_name}: {e}", exc_info=True
            )
            raise

    def download_blob_as_text(self, bucket_name: str, blob_name: str) -> str:
        """
        Downloads a specific file's content as a text string.
        :param bucket_name: The name of the GCS bucket.
        :param blob_name: The name (path) of the file in the bucket.
        """
        try:
            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            return blob.download_as_text()
        except GoogleAPICallError as e:
            logger.error(
                f"GCP API error downloading blob {blob_name}: {e}", exc_info=True
            )
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error downloading blob {blob_name}: {e}", exc_info=True
            )
            raise


# --- 2. Stub/Placeholder Client Classes (to match the project pattern) ---
# These are like SlackRESTClientViaUsernamePassword
class GoogleCloudRESTClientViaUsernamePassword:
    def __init__(self, username: str, password: str) -> None:
        logger.warning("Google Cloud Storage does not support username/password auth.")
        raise NotImplementedError

    def get_storage_client(self) -> storage.Client:
        raise NotImplementedError


class GoogleCloudRESTClientViaApiKey:
    def __init__(self, api_key: str) -> None:
        logger.warning(
            "Google Cloud Storage does not support simple API Key auth. Use a Service Account."
        )
        raise NotImplementedError

    def get_storage_client(self) -> storage.Client:
        raise NotImplementedError


# --- 3. The Real Config Dataclass ---
# This is like SlackTokenConfig
@dataclass
class GoogleCloudServiceAccountConfig:
    """
    Configuration for the GCS client via a service account JSON file.
    """

    service_account_json_path: str
    ssl: bool = True  # Added to match the pattern, GCS forces SSL.

    def create_client(self) -> GoogleCloudRESTClientViaServiceAccount:
        """
        Creates the real GCS client.
        """
        return GoogleCloudRESTClientViaServiceAccount(self.service_account_json_path)

    def to_dict(self) -> dict:
        return asdict(self)


# --- 4. Stub Config Dataclasses (to match the project pattern) ---
@dataclass
class GoogleCloudUsernamePasswordConfig:
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> GoogleCloudRESTClientViaUsernamePassword:
        return GoogleCloudRESTClientViaUsernamePassword(self.username, self.password)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GoogleCloudApiKeyConfig:
    api_key: str
    ssl: bool = False

    def create_client(self) -> GoogleCloudRESTClientViaApiKey:
        return GoogleCloudRESTClientViaApiKey(self.api_key)

    def to_dict(self) -> dict:
        return asdict(self)


# --- 5. The Final Builder Class (This is the "GoogleCloudClient") ---
# This is the main class the issue asks for, matching the SlackClient pattern.
class GoogleCloudClient(IClient):
    """
    Builder class for Google Cloud Storage clients.
    This class is what the application will interact with.
    """

    def __init__(
        self,
        client: Union[
            GoogleCloudRESTClientViaServiceAccount,
            GoogleCloudRESTClientViaUsernamePassword,
            GoogleCloudRESTClientViaApiKey,
        ],
    ) -> None:
        """
        Initialize with a GCS client object.
        """
        self.client = client

    def get_client(
        self,
    ) -> Union[
        GoogleCloudRESTClientViaServiceAccount,
        GoogleCloudRESTClientViaUsernamePassword,
        GoogleCloudRESTClientViaApiKey,
    ]:
        """
        Return the raw client object.
        """
        return self.client

    def get_storage_client(self) -> storage.Client:
        """
        Return the underlying official SDK client.
        """
        # Note: The client methods list_buckets, list_blobs, download_blob_as_text are on
        # the wrapper class, not the storage.Client object returned here.
        # This method is primarily used internally by the client wrapper itself.
        if isinstance(self.client, GoogleCloudRESTClientViaServiceAccount):
            return self.client.get_storage_client()
        raise NotImplementedError("Cannot get storage client for stubbed auth types.")

    @classmethod
    def build_with_config(
        cls,
        config: Union[
            GoogleCloudServiceAccountConfig,
            GoogleCloudUsernamePasswordConfig,
            GoogleCloudApiKeyConfig,
        ],
    ) -> "GoogleCloudClient":
        """
        Build GoogleCloudClient with a configuration object.
        This is what your example.py will use.
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> "GoogleCloudClient":
        """
        Build GoogleCloudClient using the application's configuration service.
        This is what the main app will use.
        """
        # TODO: Implement the logic to fetch GCS config from the config_service
        # For now, we raise NotImplementedError as per the pattern
        raise NotImplementedError("build_from_services is not yet implemented for GCS")
