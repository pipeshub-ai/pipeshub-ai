"""DocuSign client for the PipesHub platform."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from docusign_esign import (
    ApiClient,
    AuthenticationApi,
    EnvelopesApi,
    TemplatesApi,
    UsersApi,
)
from docusign_esign.client.auth.oauth import OAuthToken


class DocuSignError(Exception):
    """Base exception for DocuSign client errors."""



class AuthenticationError(DocuSignError):
    """Authentication error."""



class RequestError(DocuSignError):
    """Request error."""



@dataclass
class DocuSignTokenConfig:
    """Configuration for DocuSign client using an access token."""

    account_id: str
    client_id: str
    client_secret: str
    access_token: str
    base_path: str = "https://demo.docusign.net/restapi"


@dataclass
class DocuSignJWTConfig:
    """Configuration for DocuSign client using JWT authentication."""

    account_id: str
    client_id: str
    user_id: str
    private_key_file: str
    base_path: str = "https://demo.docusign.net/restapi"


class DocuSignClient:
    """DocuSign client using the official DocuSign SDK."""

    def __init__(
        self,
        account_id: str,
        client_id: str,
        client_secret: str,
        access_token: str | None = None,
        refresh_token: str | None = None,
        base_path: str = "https://demo.docusign.net/restapi",
        private_key_file: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Initialize DocuSign client.

        Args:
            account_id: DocuSign account ID
            client_id: DocuSign integration key (client ID)
            client_secret: DocuSign client secret
            access_token: OAuth access token (Optional if using JWT)
            refresh_token: OAuth refresh token (Optional if using JWT)
            base_path: API base path (demo or production)
            private_key_file: Path to private key file for JWT auth
            user_id: User ID for JWT impersonation

        """
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.base_path = base_path
        self.private_key_file = private_key_file
        self.user_id = user_id

        # Initialize API client
        self.api_client = ApiClient()
        self.api_client.host = base_path

        # Authenticate
        self._authenticate()

        # Initialize API instances
        self.envelopes_api = EnvelopesApi(self.api_client)
        self.templates_api = TemplatesApi(self.api_client)
        self.users_api = UsersApi(self.api_client)
        self.auth_api = AuthenticationApi(self.api_client)

    @classmethod
    def build_with_config(cls, config: DocuSignTokenConfig | DocuSignJWTConfig) -> DocuSignClient:
        """Build client with configuration object.

        Args:
            config: Configuration object

        Returns:
            Configured DocuSign client

        """
        if isinstance(config, DocuSignTokenConfig):
            return cls(
                account_id=config.account_id,
                client_id=config.client_id,
                client_secret=config.client_secret,
                access_token=config.access_token,
                base_path=config.base_path,
            )
        if isinstance(config, DocuSignJWTConfig):
            return cls(
                account_id=config.account_id,
                client_id=config.client_id,
                client_secret="",  # Not needed for JWT
                private_key_file=config.private_key_file,
                user_id=config.user_id,
                base_path=config.base_path,
            )
        raise ValueError(f"Unsupported configuration type: {type(config)}")

    def get_client(self) -> Any:
        """Return the API client.

        Returns:
            DocuSign API client

        """
        return self.api_client

    def _authenticate(self) -> None:
        """Authenticate with DocuSign using available credentials."""
        try:
            if self.access_token:
                self._authenticate_with_oauth()
            elif self.private_key_file:
                self._authenticate_with_jwt()
            else:
                raise AuthenticationError("Either access_token or private_key_file must be provided")
        except Exception as e:
            error_msg = f"Authentication failed: {e!s}"
            raise AuthenticationError(error_msg) from e

    def _authenticate_with_oauth(self) -> None:
        """Authenticate using OAuth access token."""
        try:
            oauth_token = OAuthToken(access_token=self.access_token)
            self.api_client.set_default_header("Authorization", f"Bearer {self.access_token}")
            self.api_client.rest_client.token = oauth_token
        except Exception as e:
            error_msg = f"OAuth authentication failed: {e!s}"
            raise AuthenticationError(error_msg) from e

    def _authenticate_with_jwt(self) -> None:
        """Authenticate using JWT flow with private key."""
        try:
            self.api_client.configure_jwt_authorization_flow(
                private_key_file=self.private_key_file,
                oauth_base_url=self.base_path,
                client_id=self.client_id,
                user_id=self.user_id,
                oauth_host_name="account-d.docusign.com",
                expires_in=3600,
                scopes=["signature", "impersonation"],
            )
        except Exception as e:
            error_msg = f"JWT authentication failed: {e!s}"
            raise AuthenticationError(error_msg) from e

    def refresh_access_token(self) -> None:
        """Refresh the OAuth access token using refresh token."""
        if not self.refresh_token:
            raise AuthenticationError("Refresh token not provided")

        try:
            # Implementation depends on DocuSign SDK specifics
            oauth_token = self.api_client.request_jwt_user_token(
                client_id=self.client_id,
                user_id=self.user_id,
                oauth_host_name="account-d.docusign.com",
                private_key_file=self.private_key_file,
                expires_in=3600,
                scopes=["signature", "impersonation"],
            )

            self.access_token = oauth_token.access_token
            self._authenticate_with_oauth()
        except Exception as e:
            error_msg = f"Failed to refresh token: {e!s}"
            raise AuthenticationError(error_msg) from e

    def get_user_info(self) -> dict[str, Any]:
        """Get information about the current authenticated user.

        Returns:
            User information

        """
        try:
            # Try different approaches to get user information
            try:
                # First try getting current user
                user_info = self.users_api.get_information(self.account_id, "current")
                return user_info.to_dict()
            except Exception:
                # Fall back to get_users which returns a list of users
                users = self.users_api.list(self.account_id)
                # Return the first user as the current user
                if users and hasattr(users, "users") and users.users:
                    return users.users[0].to_dict()
                # Last resort: return a minimal user info dictionary
                return {"account_id": self.account_id, "email": "Unknown", "name": "Unknown"}
        except Exception as e:
            error_msg = f"Failed to get user info: {e!s}"
            raise RequestError(error_msg) from e

    def list_envelopes(
        self,
        from_date: str | None = None,
        status: str | None = None,
        count: int = 100,
    ) -> dict[str, Any]:
        """List envelopes in the user's account.

        Args:
            from_date: Start date for envelope filter in format YYYY-MM-DD or YYYY-MM-DDThh:mm:ssZ
            status: Filter by envelope status
            count: Maximum number of envelopes to return

        Returns:
            List of envelopes

        """
        try:
            # Format from_date according to DocuSign API requirements
            from_date = from_date or "2020-01-01T00:00:00.000Z"
            if from_date and "T" not in from_date:
                from_date = f"{from_date}T00:00:00.000Z"

            options = {
                "from_date": from_date,
                "count": count,
            }
            if status:
                options["status"] = status

            envelopes = self.envelopes_api.list_status_changes(
                self.account_id,
                **options,
            )
            return envelopes.to_dict()
        except Exception as e:
            error_msg = f"Failed to list envelopes: {e!s}"
            raise RequestError(error_msg) from e

    def get_envelope(self, envelope_id: str) -> dict[str, Any]:
        """Get details of a specific envelope.

        Args:
            envelope_id: The ID of the envelope to retrieve

        Returns:
            Envelope details

        """
        try:
            envelope = self.envelopes_api.get_envelope(
                self.account_id,
                envelope_id,
            )
            return envelope.to_dict()
        except Exception as e:
            error_msg = f"Failed to get envelope {envelope_id}: {e!s}"
            raise RequestError(error_msg) from e

    def list_templates(self, count: int = 100) -> dict[str, Any]:
        """List templates in the user's account.

        Args:
            count: Maximum number of templates to return

        Returns:
            List of templates

        """
        try:
            templates = self.templates_api.list_templates(
                self.account_id,
                count=count,
            )
            return templates.to_dict()
        except Exception as e:
            error_msg = f"Failed to list templates: {e!s}"
            raise RequestError(error_msg) from e

    def get_template(self, template_id: str) -> dict[str, Any]:
        """Get details of a specific template.

        Args:
            template_id: The ID of the template to retrieve

        Returns:
            Template details

        """
        try:
            template = self.templates_api.get(
                self.account_id,
                template_id,
            )
            return template.to_dict()
        except Exception as e:
            error_msg = f"Failed to get template {template_id}: {e!s}"
            raise RequestError(error_msg) from e

    def get_envelope_recipients(self, envelope_id: str) -> dict[str, Any]:
        """Get recipients of a specific envelope.

        Args:
            envelope_id: The ID of the envelope

        Returns:
            Envelope recipients information

        """
        try:
            recipients = self.envelopes_api.list_recipients(
                self.account_id,
                envelope_id,
            )
            return recipients.to_dict()
        except Exception as e:
            error_msg = f"Failed to get recipients for envelope {envelope_id}: {e!s}"
            raise RequestError(error_msg) from e

    def get_envelope_documents(self, envelope_id: str) -> dict[str, Any]:
        """Get documents of a specific envelope.

        Args:
            envelope_id: The ID of the envelope

        Returns:
            Envelope documents information

        """
        try:
            documents = self.envelopes_api.list_documents(
                self.account_id,
                envelope_id,
            )
            return documents.to_dict()
        except Exception as e:
            error_msg = f"Failed to get documents for envelope {envelope_id}: {e!s}"
            raise RequestError(error_msg) from e

    def download_document(
        self,
        envelope_id: str,
        document_id: str,
        path: str | None = None,
    ) -> str | bytes:
        """Download a document from an envelope.

        Args:
            envelope_id: The ID of the envelope
            document_id: The ID of the document
            path: Path to save the document (optional)

        Returns:
            Document content or path to saved file

        """
        try:
            document_path = self.envelopes_api.get_document(
                self.account_id,
                envelope_id,
                document_id,
            )

            if path:
                import shutil
                shutil.move(document_path, path)
                return path
            
            with open(document_path, "rb") as f:
                return f.read()
        except Exception as e:
            error_msg = f"Failed to download document {document_id} from envelope {envelope_id}: {e!s}"
            raise RequestError(error_msg) from e
