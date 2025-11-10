"""DocuSign API Client using official Python SDK.

This module provides a wrapper around the official DocuSign eSignature Python SDK
for easier integration with the PipesHub platform.
"""

from typing import Any

from docusign_esign import (
    ApiClient,
    ApiException,
    EnvelopesApi,
    FoldersApi,
    TemplatesApi,
    UsersApi,
)


class DocuSignClientError(Exception):
    """Base exception for DocuSign client errors."""


class DocuSignClient:
    """Client for interacting with DocuSign eSignature API.

    This client wraps the official DocuSign Python SDK and provides
    simplified methods for common operations.

    Attributes:
        account_id: DocuSign account identifier
        client: DocuSign ApiClient instance
        envelopes: EnvelopesApi instance for envelope operations
        templates: TemplatesApi instance for template operations
        users: UsersApi instance for user operations
        folders: FoldersApi instance for folder operations
    """

    def __init__(
        self,
        access_token: str,
        base_uri: str,
        account_id: str,
    ) -> None:
        """Initialize the DocuSign client.

        Args:
            access_token: OAuth access token for authentication
            base_uri: Base URI for the DocuSign API
            account_id: DocuSign account ID

        Raises:
            DocuSignClientError: If initialization fails
        """
        try:
            self.account_id = account_id
            self.client = ApiClient()
            self.client.host = f"{base_uri}/restapi"
            self.client.set_default_header(
                "Authorization", f"Bearer {access_token}"
            )

            # Initialize API instances
            self.envelopes = EnvelopesApi(self.client)
            self.templates = TemplatesApi(self.client)
            self.users = UsersApi(self.client)
            self.folders = FoldersApi(self.client)
        except Exception as e:
            msg = f"Failed to initialize DocuSign client: {e}"
            raise DocuSignClientError(msg) from e

    def list_envelopes(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        status: str | None = None,
        folder_ids: str | None = None,
        count: str = "100",
    ) -> dict[str, Any]:
        """List envelopes with optional filters.

        Args:
            from_date: Start date for filtering (ISO 8601 format)
            to_date: End date for filtering (ISO 8601 format)
            status: Filter by envelope status
            folder_ids: Comma-separated list of folder IDs to filter by
            count: Maximum number of results to return (default: 100)

        Returns:
            Dictionary containing envelope list and metadata

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            kwargs: dict[str, Any] = {"count": count}
            if from_date:
                kwargs["from_date"] = from_date
            if to_date:
                kwargs["to_date"] = to_date
            if status:
                kwargs["status"] = status
            if folder_ids:
                kwargs["folder_ids"] = folder_ids

            response = self.envelopes.list_status_changes(
                self.account_id, **kwargs
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list envelopes: {e}"
            raise DocuSignClientError(msg) from e

    def get_envelope(self, envelope_id: str) -> dict[str, Any]:
        """Get details of a specific envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing envelope details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.get_envelope(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    def get_envelope_documents(self, envelope_id: str) -> dict[str, Any]:
        """List all documents in an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing document list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.list_documents(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get documents for envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    def download_document(
        self,
        envelope_id: str,
        document_id: str,
    ) -> bytes:
        """Download a specific document from an envelope.

        Args:
            envelope_id: The envelope ID
            document_id: The document ID

        Returns:
            Document content as bytes

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            return self.envelopes.get_document(
                self.account_id,
                document_id,
                envelope_id,
            )
        except ApiException as e:
            msg = (
                f"Failed to download document {document_id} "
                f"from envelope {envelope_id}: {e}"
            )
            raise DocuSignClientError(msg) from e

    def list_templates(
        self,
        count: str = "100",
        folder_ids: str | None = None,
        search_text: str | None = None,
    ) -> dict[str, Any]:
        """List templates in the account.

        Args:
            count: Maximum number of results to return (default: 100)
            folder_ids: Comma-separated list of folder IDs to filter by
            search_text: Search text to filter templates

        Returns:
            Dictionary containing template list and metadata

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            kwargs: dict[str, Any] = {"count": count}
            if folder_ids:
                kwargs["folder_ids"] = folder_ids
            if search_text:
                kwargs["search_text"] = search_text

            response = self.templates.list_templates(
                self.account_id, **kwargs
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list templates: {e}"
            raise DocuSignClientError(msg) from e

    def get_template(self, template_id: str) -> dict[str, Any]:
        """Get details of a specific template.

        Args:
            template_id: The template ID

        Returns:
            Dictionary containing template details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.templates.get(self.account_id, template_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get template {template_id}: {e}"
            raise DocuSignClientError(msg) from e

    def list_folders(self) -> dict[str, Any]:
        """List all folders in the account.

        Returns:
            Dictionary containing folder list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.folders.list(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list folders: {e}"
            raise DocuSignClientError(msg) from e

    def list_users(
        self,
        count: str = "100",
        start_position: str = "0",
    ) -> dict[str, Any]:
        """List users in the account.

        Args:
            count: Maximum number of results to return (default: 100)
            start_position: Starting position for pagination (default: 0)

        Returns:
            Dictionary containing user list and metadata

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.users.list(
                self.account_id,
                count=count,
                start_position=start_position,
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list users: {e}"
            raise DocuSignClientError(msg) from e

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Get details of a specific user.

        Args:
            user_id: The user ID

        Returns:
            Dictionary containing user details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.users.get_information(self.account_id, user_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get user {user_id}: {e}"
            raise DocuSignClientError(msg) from e