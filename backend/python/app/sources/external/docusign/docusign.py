# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
from __future__ import annotations

import docusign_esign  # type: ignore[reportMissingImports]
from docusign_esign import ApiClient  # type: ignore[reportMissingImports]
from typing import Dict, Optional, Union, cast

from app.sources.client.docusign.docusign import DocuSignResponse


class DocuSignDataSource:
    """
    Strict, typed wrapper over the docusign-esign SDK for common DocuSign
    eSignature business operations.

    Accepts either a ``docusign_esign.ApiClient`` instance *or* any object with
    ``.get_sdk() -> ApiClient`` and ``.get_account_id() -> str``.

    Lazily creates per-resource API objects (EnvelopesApi, TemplatesApi, etc.)
    so that only the resources you actually call are instantiated.
    """

    def __init__(self, client_or_sdk: Union[ApiClient, object]) -> None:
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: ApiClient = cast(ApiClient, sdk_obj)
        else:
            self._sdk = cast(ApiClient, client_or_sdk)

        # Resolve account_id
        if hasattr(client_or_sdk, "get_account_id"):
            self._account_id: str = str(getattr(client_or_sdk, "get_account_id")())
        else:
            self._account_id = ""

        # Lazy API instances
        self._envelopes_api: Optional[docusign_esign.EnvelopesApi] = None
        self._templates_api: Optional[docusign_esign.TemplatesApi] = None
        self._users_api: Optional[docusign_esign.UsersApi] = None
        self._folders_api: Optional[docusign_esign.FoldersApi] = None
        self._accounts_api: Optional[docusign_esign.AccountsApi] = None

    # ---- lazy accessors ----

    @property
    def envelopes_api(self) -> docusign_esign.EnvelopesApi:
        if self._envelopes_api is None:
            self._envelopes_api = docusign_esign.EnvelopesApi(self._sdk)
        return self._envelopes_api

    @property
    def templates_api(self) -> docusign_esign.TemplatesApi:
        if self._templates_api is None:
            self._templates_api = docusign_esign.TemplatesApi(self._sdk)
        return self._templates_api

    @property
    def users_api(self) -> docusign_esign.UsersApi:
        if self._users_api is None:
            self._users_api = docusign_esign.UsersApi(self._sdk)
        return self._users_api

    @property
    def folders_api(self) -> docusign_esign.FoldersApi:
        if self._folders_api is None:
            self._folders_api = docusign_esign.FoldersApi(self._sdk)
        return self._folders_api

    @property
    def accounts_api(self) -> docusign_esign.AccountsApi:
        if self._accounts_api is None:
            self._accounts_api = docusign_esign.AccountsApi(self._sdk)
        return self._accounts_api

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        """Filter out Nones to avoid overriding SDK defaults."""
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
    def list_envelopes(self, from_date: str, to_date: Optional[str] = None, status: Optional[str] = None, search_text: Optional[str] = None, count: Optional[str] = None, start_position: Optional[str] = None, order: Optional[str] = None, order_by: Optional[str] = None, folder_ids: Optional[str] = None) -> DocuSignResponse:
        """List envelopes for the account. from_date is required by the API.  [envelopes]"""
        try:
            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position, order=order, order_by=order_by, folder_ids=folder_ids)
            result = self.envelopes_api.list_status_changes(account_id=self._account_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def get_envelope(self, envelope_id: str) -> DocuSignResponse:
        """Get details for a specific envelope.  [envelopes]"""
        try:
            result = self.envelopes_api.get_envelope(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def create_envelope(self, envelope_definition: Dict[str, object]) -> DocuSignResponse:
        """Create and optionally send a new envelope from an envelope definition dict.  [envelopes]"""
        try:
            body = docusign_esign.EnvelopeDefinition(**envelope_definition)
            result = self.envelopes_api.create_envelope(account_id=self._account_id, envelope_definition=body)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def update_envelope(self, envelope_id: str, envelope: Dict[str, object]) -> DocuSignResponse:
        """Update an existing envelope (e.g. change status to sent or voided).  [envelopes]"""
        try:
            body = docusign_esign.Envelope(**envelope)
            result = self.envelopes_api.update(account_id=self._account_id, envelope_id=envelope_id, envelope=body)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_envelope_documents(self, envelope_id: str) -> DocuSignResponse:
        """List documents in an envelope.  [envelopes]"""
        try:
            result = self.envelopes_api.list_documents(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def get_envelope_document(self, envelope_id: str, document_id: str) -> DocuSignResponse:
        """Download a specific document from an envelope.  [envelopes]"""
        try:
            result = self.envelopes_api.get_document(account_id=self._account_id, envelope_id=envelope_id, document_id=document_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_envelope_recipients(self, envelope_id: str) -> DocuSignResponse:
        """List recipients for an envelope.  [envelopes]"""
        try:
            result = self.envelopes_api.list_recipients(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def get_envelope_audit_events(self, envelope_id: str) -> DocuSignResponse:
        """Get audit trail events for an envelope.  [envelopes]"""
        try:
            result = self.envelopes_api.list_audit_events(account_id=self._account_id, envelope_id=envelope_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_templates(self, count: Optional[str] = None, start_position: Optional[str] = None, search_text: Optional[str] = None, folder: Optional[str] = None, order: Optional[str] = None, order_by: Optional[str] = None) -> DocuSignResponse:
        """List templates for the account.  [templates]"""
        try:
            params = self._params(count=count, start_position=start_position, search_text=search_text, folder=folder, order=order, order_by=order_by)
            result = self.templates_api.list_templates(account_id=self._account_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def get_template(self, template_id: str) -> DocuSignResponse:
        """Get details for a specific template.  [templates]"""
        try:
            result = self.templates_api.get(account_id=self._account_id, template_id=template_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_users(self, count: Optional[str] = None, start_position: Optional[str] = None, status: Optional[str] = None, email: Optional[str] = None) -> DocuSignResponse:
        """List users in the account.  [users]"""
        try:
            params = self._params(count=count, start_position=start_position, status=status, email=email)
            result = self.users_api.list(account_id=self._account_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def get_user(self, user_id: str) -> DocuSignResponse:
        """Get details for a specific user.  [users]"""
        try:
            result = self.users_api.get_information(account_id=self._account_id, user_id=user_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_folders(self) -> DocuSignResponse:
        """List folders in the account.  [folders]"""
        try:
            result = self.folders_api.list(account_id=self._account_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_folder_items(self, folder_id: str, from_date: Optional[str] = None, to_date: Optional[str] = None, status: Optional[str] = None, search_text: Optional[str] = None, count: Optional[str] = None, start_position: Optional[str] = None) -> DocuSignResponse:
        """List items (envelopes) in a specific folder.  [folders]"""
        try:
            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position)
            result = self.folders_api.list_items(account_id=self._account_id, folder_id=folder_id, **params)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_brands(self) -> DocuSignResponse:
        """List brands for the account.  [accounts]"""
        try:
            result = self.accounts_api.list_brands(account_id=self._account_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")
    def list_custom_fields(self) -> DocuSignResponse:
        """List custom fields for the account.  [accounts]"""
        try:
            result = self.accounts_api.list_custom_fields(account_id=self._account_id)
            return DocuSignResponse(success=True, data=result)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")

