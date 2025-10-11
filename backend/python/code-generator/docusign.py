"""DocuSign data source code generator."""
from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional, Set, Union

# Move into type-checking block
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.sources.client.docusign.docusign import DocuSignClient

from app.sources.external.base import BaseDataSource, register_data_source


@register_data_source("docusign")
class DocuSignDataSource(BaseDataSource):
    """DocuSign data source for code generation."""
    
    def __init__(self, client: "DocuSignClient") -> None:
        """Initialize the DocuSign data source.
        
        Args:
            client: An authenticated DocuSign client
        """
        self.client = client
        self._methods = self._get_client_methods()
    
    def _get_client_methods(self) -> Set[str]:
        """Get the list of available client methods.
        
        Returns:
            Set of method names available in the client
        """
        return {
            name for name, _ in inspect.getmembers(
                self.client.__class__, 
                predicate=inspect.isfunction
            ) if not name.startswith('_')
        }
    
    def _check_method(self, method_name: str) -> None:
        """Check if a method exists in the client.
        
        Args:
            method_name: Name of the method to check
        
        Raises:
            ValueError: If the method doesn't exist
        """
        if method_name not in self._methods:
            error_msg = f"Method {method_name} not found in DocuSignClient"
            raise ValueError(error_msg)

    # User Management Methods
    
    def get_user_info(self) -> Dict[str, Any]:
        """Get information about the current authenticated user.
        
        Returns:
            User information
        """
        self._check_method("get_user_info")
        return self.client.get_user_info()
    
    # Envelope Methods
    
    def list_envelopes(
        self, 
        from_date: Optional[str] = None,
        status: Optional[str] = None,
        count: int = 100
    ) -> Dict[str, Any]:
        """List envelopes in the user's account.
        
        Args:
            from_date: Start date for envelope filter in format YYYY-MM-DD
            status: Filter by envelope status
            count: Maximum number of envelopes to return
            
        Returns:
            List of envelopes
        """
        self._check_method("list_envelopes")
        return self.client.list_envelopes(
            from_date=from_date, 
            status=status, 
            count=count
        )
    
    def get_envelope(self, envelope_id: str) -> Dict[str, Any]:
        """Get details of a specific envelope.
        
        Args:
            envelope_id: The ID of the envelope to retrieve
            
        Returns:
            Envelope details
        """
        self._check_method("get_envelope")
        return self.client.get_envelope(envelope_id=envelope_id)
    
    # Template Methods
    
    def list_templates(self, count: int = 100) -> Dict[str, Any]:
        """List templates in the user's account.
        
        Args:
            count: Maximum number of templates to return
            
        Returns:
            List of templates
        """
        self._check_method("list_templates")
        return self.client.list_templates(count=count)
    
    def get_template(self, template_id: str) -> Dict[str, Any]:
        """Get details of a specific template.
        
        Args:
            template_id: The ID of the template to retrieve
            
        Returns:
            Template details
        """
        self._check_method("get_template")
        return self.client.get_template(template_id=template_id)
    
    # Recipient Methods
    
    def get_envelope_recipients(self, envelope_id: str) -> Dict[str, Any]:
        """Get recipients of a specific envelope.
        
        Args:
            envelope_id: The ID of the envelope
            
        Returns:
            Envelope recipients information
        """
        self._check_method("get_envelope_recipients")
        return self.client.get_envelope_recipients(envelope_id=envelope_id)
    
    # Document Methods
    
    def get_envelope_documents(self, envelope_id: str) -> Dict[str, Any]:
        """Get documents of a specific envelope.
        
        Args:
            envelope_id: The ID of the envelope
            
        Returns:
            Envelope documents information
        """
        self._check_method("get_envelope_documents")
        return self.client.get_envelope_documents(envelope_id=envelope_id)
    
    def download_document(
        self, 
        envelope_id: str, 
        document_id: str, 
        path: Optional[str] = None
    ) -> Union[str, bytes]:
        """Download a document from an envelope.
        
        Args:
            envelope_id: The ID of the envelope
            document_id: The ID of the document
            path: Path to save the document (optional)
            
        Returns:
            Document content or path to saved file
        """
        self._check_method("download_document")
        return self.client.download_document(
            envelope_id=envelope_id,
            document_id=document_id,
            path=path
        )