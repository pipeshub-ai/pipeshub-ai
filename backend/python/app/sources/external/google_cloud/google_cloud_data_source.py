# google_cloud_data_source.py
from typing import Any, Dict, List
from app.sources.client.google_cloud.google_cloud import GoogleCloudClient

class GoogleCloudDataSource:
    """
    Data source class using GoogleCloudClient to interact with GCS.
    """

    def __init__(self, client: GoogleCloudClient):
        self.client = client

    def get_all_buckets(self) -> List[str]:
        """Return all GCS buckets."""
        return self.client.list_buckets()

    def upload_to_bucket(self, bucket: str, local_path: str, remote_path: str) -> str:
        """Upload file."""
        return self.client.upload_file(bucket, local_path, remote_path)

    def download_from_bucket(self, bucket: str, blob_name: str, local_dest: str) -> str:
        """Download file."""
        return self.client.download_file(bucket, blob_name, local_dest)
