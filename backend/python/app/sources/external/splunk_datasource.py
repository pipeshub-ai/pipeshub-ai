import logging

from app.sources.client.splunk.splunk import SplunkClient


class SplunkDataSource:
    def __init__(self, client: SplunkClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client
        self.logger = logging.getLogger(__name__)
        if self._client is None:
            raise ValueError("Splunk client is not initialized")

    def get_data_source(self) -> "SplunkDataSource":
        return self

    def search(self, query: str) -> str:
        """Run a search query using SplunkClient.

        Args:
            query: The Splunk search query string
        Returns:
            The search job ID (SID)

        """
        if self._client is None:
            raise ValueError("Splunk client is not initialized")
        try:
            self.logger.info(f"Executing Splunk search query: {query}")
            job_id = self._client.run_search(query)
            self.logger.info(f"Search query executed successfully, job ID: {job_id}")
            return job_id
        except Exception as e:
            self.logger.error(f"Failed to execute Splunk search query: {e!s}")
            raise

