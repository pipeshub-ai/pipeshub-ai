"""DocuSign data source implementation."""

import logging
from typing import Any, Dict, Optional, Callable, TypeVar
from functools import wraps

from app.sources.client.docusign.docusign import DocuSignClient

# Set up logger
logger = logging.getLogger(__name__)

T = TypeVar("T")


class DocuSignResponse:
    """Standardized response wrapper for DocuSign API calls."""

    def __init__(
        self, 
        success: bool, 
        data: Any = None, 
        error: str = None
    ) -> None:
        """Initialize DocuSign response.

        Args:
            success: Whether the request was successful
            data: Response data (if successful)
            error: Error message (if unsuccessful)
        """
        self.success = success
        self.data = data
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        """Return a dictionary representation of the response."""
        return {"success": self.success, "data": self.data, "error": self.error}


def handle_api_error(func: Callable[..., T]) -> Callable[..., DocuSignResponse]:
    """Standardize error handling for DocuSign API calls.
    
    Wraps API methods to provide consistent error handling.
    """

    @wraps(func)
    def wrapper(
        self: Any, 
        *args: Any, 
        **kwargs: Any
    ) -> DocuSignResponse:
        try:
            return func(self, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            logger.exception("DocuSign API error in %s", func.__name__)
            return DocuSignResponse(success=False, error=str(e))

    return wrapper


class DocuSignDataSource:
    """DocuSign data source for integration with PipesHub.

    - Uses DocuSignClient to interact with the DocuSign API
    - Provides methods for envelopes, templates, recipients, and documents
    - Standardized response format with success/data/error
    """

    def __init__(self, client: DocuSignClient) -> None:
        """Initialize DocuSign data source with client.

        Args:
            client: DocuSignClient instance
        """
        self.client = client

    def get_data_source(self) -> "DocuSignDataSource":
        """Return the data source instance."""
        return self

    # ----------------------------------------------------------------------
    # User Info
    # ----------------------------------------------------------------------
    @handle_api_error
    def get_user_info(self) -> DocuSignResponse:
        """Get information about the current authenticated user."""
        user_info = self.client.get_user_info()
        return DocuSignResponse(success=True, data=user_info)

    # ----------------------------------------------------------------------
    # Envelopes
    # ----------------------------------------------------------------------
    @handle_api_error
    def list_envelopes(
        self,
        from_date: Optional[str] = None,
        status: Optional[str] = None,
        count: int = 100,
    ) -> DocuSignResponse:
        """List envelopes in the user's account."""
        envelopes = self.client.list_envelopes(
            from_date=from_date, status=status, count=count
        )
        return DocuSignResponse(success=True, data=envelopes)

    @handle_api_error
    def get_envelope(self, envelope_id: str) -> DocuSignResponse:
        """Get details of a specific envelope."""
        envelope = self.client.get_envelope(envelope_id=envelope_id)
        return DocuSignResponse(success=True, data=envelope)

    @handle_api_error
    def get_envelope_recipients(self, envelope_id: str) -> DocuSignResponse:
        """Get recipients of a specific envelope."""
        recipients = self.client.get_envelope_recipients(envelope_id=envelope_id)
        return DocuSignResponse(success=True, data=recipients)

    @handle_api_error
    def get_envelope_documents(self, envelope_id: str) -> DocuSignResponse:
        """Get documents of a specific envelope."""
        documents = self.client.get_envelope_documents(envelope_id=envelope_id)
        return DocuSignResponse(success=True, data=documents)

    @handle_api_error
    def download_document(
        self,
        envelope_id: str,
        document_id: str,
        path: Optional[str] = None,
    ) -> DocuSignResponse:
        """Download a document from an envelope."""
        result = self.client.download_document(
            envelope_id=envelope_id, document_id=document_id, path=path
        )
        return DocuSignResponse(success=True, data=result)

    # ----------------------------------------------------------------------
    # Templates
    # ----------------------------------------------------------------------
    @handle_api_error
    def list_templates(self, count: int = 100) -> DocuSignResponse:
        """List templates in the user's account."""
        templates = self.client.list_templates(count=count)
        return DocuSignResponse(success=True, data=templates)

    @handle_api_error
    def get_template(self, template_id: str) -> DocuSignResponse:
        """Get details of a specific template."""
        template = self.client.get_template(template_id=template_id)
        return DocuSignResponse(success=True, data=template)
