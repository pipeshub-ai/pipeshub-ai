"""DocuSign data source implementation for PipesHub.

This module provides the data source interface for integrating DocuSign
with the PipesHub platform.
"""

from dataclasses import dataclass
from typing import Any

from app.sources.client.docusign.docusign import DocuSignClient


@dataclass
class DocuSignDataSource:
    """Data source for DocuSign integration.

    This class provides methods to fetch data from DocuSign for indexing
    and search purposes in the PipesHub platform.

    Attributes:
        client: DocuSignClient instance for API communication
    """

    client: DocuSignClient

    def list_envelopes(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        status: str | None = None,
        folder_ids: str | None = None,
        count: str = "100",
    ) -> dict[str, Any]:
        """Retrieve list of envelopes.

        Args:
            from_date: Start date for filtering (ISO 8601 format)
            to_date: End date for filtering (ISO 8601 format)
            status: Filter by envelope status
            folder_ids: Comma-separated list of folder IDs
            count: Maximum number of results

        Returns:
            Dictionary containing envelope list

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.list_envelopes(
            from_date=from_date,
            to_date=to_date,
            status=status,
            folder_ids=folder_ids,
            count=count,
        )

    def get_envelope(self, envelope_id: str) -> dict[str, Any]:
        """Get details of a specific envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing envelope details

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.get_envelope(envelope_id)

    def get_envelope_documents(self, envelope_id: str) -> dict[str, Any]:
        """Get list of documents in an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing document list

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.get_envelope_documents(envelope_id)

    def download_document(
        self,
        envelope_id: str,
        document_id: str,
    ) -> bytes:
        """Download a specific document.

        Args:
            envelope_id: The envelope ID
            document_id: The document ID

        Returns:
            Document content as bytes

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.download_document(envelope_id, document_id)

    def list_templates(
        self,
        count: str = "100",
        folder_ids: str | None = None,
        search_text: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve list of templates.

        Args:
            count: Maximum number of results
            folder_ids: Comma-separated list of folder IDs
            search_text: Search text to filter templates

        Returns:
            Dictionary containing template list

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.list_templates(
            count=count,
            folder_ids=folder_ids,
            search_text=search_text,
        )

    def get_template(self, template_id: str) -> dict[str, Any]:
        """Get details of a specific template.

        Args:
            template_id: The template ID

        Returns:
            Dictionary containing template details

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.get_template(template_id)

    def list_folders(self) -> dict[str, Any]:
        """Retrieve list of folders.

        Returns:
            Dictionary containing folder list

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.list_folders()

    def list_users(
        self,
        count: str = "100",
        start_position: str = "0",
    ) -> dict[str, Any]:
        """Retrieve list of users.

        Args:
            count: Maximum number of results
            start_position: Starting position for pagination

        Returns:
            Dictionary containing user list

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.list_users(count=count, start_position=start_position)

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Get details of a specific user.

        Args:
            user_id: The user ID

        Returns:
            Dictionary containing user details

        Raises:
            DocuSignClientError: If the operation fails
        """
        return self.client.get_user(user_id)

    def fetch_all_envelopes(
        self,
        from_date: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all envelopes with pagination.

        This method handles pagination automatically to retrieve all envelopes.

        Args:
            from_date: Start date for filtering (ISO 8601 format)
            status: Filter by envelope status

        Returns:
            List of all envelopes

        Raises:
            DocuSignClientError: If the operation fails
        """
        all_envelopes: list[dict[str, Any]] = []
        count = 100

        while True:
            response = self.client.list_envelopes(
                from_date=from_date,
                status=status,
                count=str(count),
            )

            envelopes = response.get("envelopes", [])
            if not envelopes:
                break

            all_envelopes.extend(envelopes)

            # Check if there are more results
            result_set_size = response.get("result_set_size")
            if not result_set_size or len(envelopes) < count:
                break

        return all_envelopes

    def fetch_all_templates(
        self,
        search_text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all templates with pagination.

        This method handles pagination automatically to retrieve all templates.

        Args:
            search_text: Search text to filter templates

        Returns:
            List of all templates

        Raises:
            DocuSignClientError: If the operation fails
        """
        response = self.client.list_templates(
            count="1000",  # Max allowed
            search_text=search_text,
        )

        return response.get("envelope_templates", []) or []
