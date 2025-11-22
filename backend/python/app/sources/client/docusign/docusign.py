"""DocuSign API Client using official Python SDK.

This module provides a comprehensive wrapper around the official DocuSign
eSignature Python SDK for integration with the PipesHub platform.
"""

from typing import Any

from docusign_esign import (
    AccountsApi,
    ApiClient,
    ApiException,
    BulkEnvelopesApi,
    EnvelopesApi,
    FoldersApi,
    GroupsApi,
    TemplatesApi,
    UsersApi,
    WorkspacesApi,
)


class DocuSignClientError(Exception):
    """Base exception for DocuSign client errors."""


class DocuSignClient:
    """Comprehensive client for interacting with DocuSign eSignature API.

    This client wraps the official DocuSign Python SDK and provides
    simplified methods for all major API operations.

    Attributes:
        account_id: DocuSign account identifier
        client: DocuSign ApiClient instance
        accounts: AccountsApi instance for account operations
        envelopes: EnvelopesApi instance for envelope operations
        templates: TemplatesApi instance for template operations
        users: UsersApi instance for user operations
        folders: FoldersApi instance for folder operations
        groups: GroupsApi instance for group operations
        bulk_envelopes: BulkEnvelopesApi instance for bulk operations
        workspaces: WorkspacesApi instance for workspace operations
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

            # Initialize all API instances
            self.accounts = AccountsApi(self.client)
            self.envelopes = EnvelopesApi(self.client)
            self.templates = TemplatesApi(self.client)
            self.users = UsersApi(self.client)
            self.folders = FoldersApi(self.client)
            self.groups = GroupsApi(self.client)
            self.bulk_envelopes = BulkEnvelopesApi(self.client)
            self.workspaces = WorkspacesApi(self.client)
        except Exception as e:
            msg = f"Failed to initialize DocuSign client: {e}"
            raise DocuSignClientError(msg) from e

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
        """List envelopes with optional filters.

        Args:
            from_date: Start date for filtering (ISO 8601 format)
            to_date: End date for filtering (ISO 8601 format)
            status: Filter by envelope status
            folder_ids: Comma-separated list of folder IDs
            count: Maximum number of results (default: 100)

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

    def list_recipients(self, envelope_id: str) -> dict[str, Any]:
        """List all recipients of an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing recipient list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.list_recipients(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list recipients for envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    def get_envelope_audit_events(self, envelope_id: str) -> dict[str, Any]:
        """Get audit events for an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing audit event list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.list_audit_events(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get audit events for envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # TEMPLATE OPERATIONS
    # ========================================================================

    def list_templates(
        self,
        count: str = "100",
        folder_ids: str | None = None,
        search_text: str | None = None,
    ) -> dict[str, Any]:
        """List templates in the account.

        Args:
            count: Maximum number of results (default: 100)
            folder_ids: Comma-separated list of folder IDs
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

    # ========================================================================
    # ACCOUNT OPERATIONS
    # ========================================================================

    def get_account_information(self) -> dict[str, Any]:
        """Get account information and settings.

        Returns:
            Dictionary containing account details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.accounts.get_account_information(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get account information: {e}"
            raise DocuSignClientError(msg) from e

    def list_brands(self) -> dict[str, Any]:
        """List all brands in the account.

        Returns:
            Dictionary containing brand list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.accounts.list_brands(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list brands: {e}"
            raise DocuSignClientError(msg) from e

    def get_account_settings(self) -> dict[str, Any]:
        """Get account settings.

        Returns:
            Dictionary containing account settings

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.accounts.list_settings(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get account settings: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # USER OPERATIONS
    # ========================================================================

    def list_users(
        self,
        count: str = "100",
        start_position: str = "0",
    ) -> dict[str, Any]:
        """List users in the account.

        Args:
            count: Maximum number of results (default: 100)
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
            count: Maximum number of results (default: 100)
            start_position: Starting position for pagination

        Returns:
            Dictionary containing group list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.groups.list_groups(
                self.account_id,
                count=count,
                start_position=start_position,
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list groups: {e}"
            raise DocuSignClientError(msg) from e

    def get_group(self, group_id: str) -> dict[str, Any]:
        """Get details of a specific group.

        Args:
            group_id: The group ID

        Returns:
            Dictionary containing group details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.groups.list_groups(self.account_id)
            groups = response.to_dict().get("groups", []) or []
            for grp in groups:
                if grp.get("group_id") == group_id:
                    return grp
            raise DocuSignClientError(f"Group {group_id} not found.")
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get group {group_id}: {e}") from e

    # ========================================================================
    # FOLDER OPERATIONS
    # ========================================================================

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

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.folders.list_items(
                self.account_id,
                folder_id,
                start_position=start_position,
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list items in folder {folder_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # WORKSPACE OPERATIONS
    # ========================================================================

    def list_workspaces(self) -> dict[str, Any]:
        """List all workspaces in the account.

        Returns:
            Dictionary containing workspace list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.workspaces.list_workspaces(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list workspaces: {e}"
            raise DocuSignClientError(msg) from e

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Get details of a specific workspace.

        Args:
            workspace_id: The workspace ID

        Returns:
            Dictionary containing workspace details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.workspaces.get_workspace(
                self.account_id, workspace_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get workspace {workspace_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # BULK ENVELOPE OPERATIONS
    # ========================================================================

    def get_bulk_envelope_status(
        self,
        batch_id: str,
    ) -> dict[str, Any]:
        """Get status of a bulk envelope batch.

        Args:
            batch_id: The batch ID

        Returns:
            Dictionary containing batch status

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.bulk_envelopes.get(
                self.account_id, batch_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get bulk envelope status for batch {batch_id}: {e}"
            raise DocuSignClientError(msg) from e
