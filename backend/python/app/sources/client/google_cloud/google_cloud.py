# google_cloud.py
from google.cloud import storage  # type: ignore
from google.oauth2 import service_account
from typing import Any, Dict, Optional

class GoogleCloudClient:
    """
    Wrapper for Google Cloud SDK client initialization.
    Supports authentication via Service Account JSON or ADC.
    """

    def __init__(self, credentials_path: Optional[str] = None, project_id: Optional[str] = None):
        if credentials_path:
            creds = service_account.Credentials.from_service_account_file(credentials_path)
            self.client = storage.Client(credentials=creds, project=project_id)
        else:
            self.client = storage.Client(project=project_id)

    def list_buckets(self) -> list[str]:
        """List all storage buckets in the project."""
        return [bucket.name for bucket in self.client.list_buckets()]

    def upload_file(self, bucket_name: str, source_file: str, destination_blob: str) -> str:
        """Upload a file to a bucket."""
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(destination_blob)
        blob.upload_from_filename(source_file)
        return f"Uploaded {source_file} to {bucket_name}/{destination_blob}"

    def download_file(self, bucket_name: str, blob_name: str, destination_file: str) -> str:
        """Download a file from a bucket."""
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.download_to_filename(destination_file)
        return f"Downloaded {blob_name} to {destination_file}"
