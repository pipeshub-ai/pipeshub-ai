"""DocuSign data source implementation for PipesHub.

This module provides the data source interface for integrating DocuSign
with the PipesHub platform.
"""

from dataclasses import dataclass
from typing import Any

from app.sources.client.docusign.docusign import DocuSignClient


@dataclass
class DocuSignDataSource:
    """Comprehensive data source for DocuSign integration.

    This class provides methods to fetch data from DocuSign for indexing
    and search purposes in the PipesHub platform.

    Attributes:
        client: DocuSignClient instance for API communication
    """

    client: DocuSignClient

    # ========================================================================
    # ENVELOPE OPERATIONS
    # ========================================================================

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
        """
        return self.client.get_envelope(envelope_id)

    def get_envelope_documents(self, envelope_id: str) -> dict[str, Any]:
        """Get list of documents in an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing document list
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
        """
        return self.client.download_document(envelope_id, document_id)

    def list_recipients(self, envelope_id: str) -> dict[str, Any]:
        """List all recipients of an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing recipient list
        """
        return self.client.list_recipients(envelope_id)

    def get_envelope_audit_events(self, envelope_id: str) -> dict[str, Any]:
        """Get audit events for an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing audit event list
        """
        return self.client.get_envelope_audit_events(envelope_id)

    # ========================================================================
    # TEMPLATE OPERATIONS
    # ========================================================================

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
        """
        return self.client.get_template(template_id)

    # ========================================================================
    # ACCOUNT OPERATIONS
    # ========================================================================

    def get_account_information(self) -> dict[str, Any]:
        """Get account information and settings.

        Returns:
            Dictionary containing account details
        """
        return self.client.get_account_information()

    def list_brands(self) -> dict[str, Any]:
        """List all brands in the account.

        Returns:
            Dictionary containing brand list
        """
        return self.client.list_brands()

    def get_account_settings(self) -> dict[str, Any]:
        """Get account settings.

        Returns:
            Dictionary containing account settings
        """
        return self.client.get_account_settings()

    # ========================================================================
    # USER OPERATIONS
    # ========================================================================

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
        """
        return self.client.list_users(count=count, start_position=start_position)

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Get details of a specific user.

        Args:
            user_id: The user ID

        Returns:
            Dictionary containing user details
        """
        return self.client.get_user(user_id)

    # ========================================================================
    # GROUP OPERATIONS
    # ========================================================================

    def list_groups(
        self,
        count: str = "100",
        start_position: str = "0",
    ) -> dict[str, Any]:
        """List groups in the account.

        Args:
            count: Maximum number of results
            start_position: Starting position for pagination

        Returns:
            Dictionary containing group list
        """
        return self.client.list_groups(
            count=count,
            start_position=start_position,
        )

    def get_group(self, group_id: str) -> dict[str, Any]:
        """Get details of a specific group.

        Args:
            group_id: The group ID

        Returns:
            Dictionary containing group details
        """
        return self.client.get_group(group_id)

    # ========================================================================
    # FOLDER OPERATIONS
    # ========================================================================

    def list_folders(self) -> dict[str, Any]:
        """Retrieve list of folders.

        Returns:
            Dictionary containing folder list
        """
        return self.client.list_folders()

    def list_folder_items(
        self,
        folder_id: str,
        start_position: str = "0",
    ) -> dict[str, Any]:
        """List items in a specific folder.

        Args:
            folder_id: The folder ID
            start_position: Starting position for pagination

        Returns:
            Dictionary containing folder items
        """
        return self.client.list_folder_items(folder_id, start_position)

    # ========================================================================
    # WORKSPACE OPERATIONS
    # ========================================================================

    def list_workspaces(self) -> dict[str, Any]:
        """List all workspaces in the account.

        Returns:
            Dictionary containing workspace list
        """
        return self.client.list_workspaces()

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Get details of a specific workspace.

        Args:
            workspace_id: The workspace ID

        Returns:
            Dictionary containing workspace details
        """
        return self.client.get_workspace(workspace_id)

    # ========================================================================
    # BULK ENVELOPE OPERATIONS
    # ========================================================================

    def get_bulk_envelope_status(self, batch_id: str) -> dict[str, Any]:
        """Get status of a bulk envelope batch.

        Args:
            batch_id: The batch ID

        Returns:
            Dictionary containing batch status
        """
        return self.client.get_bulk_envelope_status(batch_id)

    # ========================================================================
    # BATCH OPERATIONS
    # ========================================================================

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
        """
        response = self.client.list_templates(
            count="1000",  # Max allowed
            search_text=search_text,
        )

        return response.get("envelope_templates", []) or []

    def fetch_all_users(self) -> list[dict[str, Any]]:
        """Fetch all users with pagination.

        Returns:
            List of all users
        """
        all_users: list[dict[str, Any]] = []
        start_position = 0
        count = 100

        while True:
            response = self.client.list_users(
                count=str(count),
                start_position=str(start_position),
            )

            users = response.get("users", [])
            if not users:
                break

            all_users.extend(users)

            end_position = response.get("end_position")
            if not end_position or len(users) < count:
                break

            start_position += count

        return all_users

    def fetch_all_groups(self) -> list[dict[str, Any]]:
        """Fetch all groups with pagination.

        Returns:
            List of all groups
        """
        all_groups: list[dict[str, Any]] = []
        start_position = 0
        count = 100

        while True:
            response = self.client.list_groups(
                count=str(count),
                start_position=str(start_position),
            )

            groups = response.get("groups", [])
            if not groups:
                break

            all_groups.extend(groups)

            end_position = response.get("end_position")
            if not end_position or len(groups) < count:
                break

            start_position += count

        return all_groups
