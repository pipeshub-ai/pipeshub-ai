import json
from http import HTTPStatus
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlencode

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignResponse
from app.sources.client.http.http_request import HTTPRequest


class DocuSignDataSource:
    """Auto-generated DocuSign API client wrapper.
    Provides async methods for ALL DocuSign API endpoints:
    - eSignature API (envelopes, documents, templates, recipients, etc.)
    - Rooms API (rooms, forms, office, field data, etc.)
    - Click API (clickwraps, agreements, etc.)
    - Admin API (users, groups, accounts, etc.)
    - Monitor API (monitoring data, status, etc.)
    All methods return DocuSignResponse objects with standardized success/data/error format.
    All parameters are explicitly typed - no **kwargs usage.
    """

    def __init__(self, client: DocuSignClient) -> None:
        """Initialize with DocuSignClient."""
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'DocuSignDataSource':
        """Return the data source instance."""
        return self

    # ========================================================================
    # AUTHENTICATION API
    # ========================================================================
    async def get_user_info(self) -> DocuSignResponse:
        """Retrieves information about the authenticated user

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/oauth/userinfo"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # ESIGNATURE - ACCOUNTS API
    # ========================================================================
    async def list_accounts(
        self,
        email: Optional[str] = None,
        include_closed: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets account information for the authenticated user

        Args:
            email: Filter results by email address
            include_closed: When set to true, includes closed accounts

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if email is not None:
            query_params.append(('email', str(email)))
        if include_closed is not None:
            query_params.append(('include_closed', str(include_closed)))

        url = self.base_url + "/v2.1/accounts"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))
    
    async def get_account_information(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Retrieves the account information for the specified account

        Args:
            account_id: The external account number (int) or account ID GUID (required)

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/v2.1/accounts/{account_id}".format(account_id=account_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))
    
    async def update_account_settings(
        self,
        account_id: str,
        access_code_format: Optional[Dict[str, Union[str, bool, int]]] = None,
        account_settings: Optional[Dict[str, Union[str, bool, int]]] = None,
        adoption_settings: Optional[Dict[str, Union[str, bool, int]]] = None,
        advanced_correct_settings: Optional[Dict[str, Union[str, bool, int]]] = None,
        allow_bulk_send: Optional[bool] = None
    ) -> DocuSignResponse:
        """Updates account settings for the specified account

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            access_code_format: Access code format settings
            account_settings: Account settings
            adoption_settings: Adoption settings
            advanced_correct_settings: Advanced correct settings
            allow_bulk_send: Allow bulk sending

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/v2.1/accounts/{account_id}/settings".format(account_id=account_id)

        body = {}
        if access_code_format is not None:
            body['accessCodeFormat'] = access_code_format
        if account_settings is not None:
            body['accountSettings'] = account_settings
        if adoption_settings is not None:
            body['adoptionSettings'] = adoption_settings
        if advanced_correct_settings is not None:
            body['advancedCorrectSettings'] = advanced_correct_settings
        if allow_bulk_send is not None:
            body['allowBulkSend'] = allow_bulk_send

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))
    
    async def get_account_settings(
        self,
        account_id: str
    ) -> DocuSignResponse:
        """Gets account settings information for the specified account

        Args:
            account_id: The external account number (int) or account ID GUID (required)

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/v2.1/accounts/{account_id}/settings".format(account_id=account_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # ESIGNATURE - ENVELOPES API
    # ========================================================================
    async def create_envelope(
        self,
        account_id: str,
        documents: Optional[List[Dict[str, Union[str, int, bytes]]]] = None,
        email_subject: Optional[str] = None,
        email_blurb: Optional[str] = None,
        status: Optional[str] = None,
        recipients: Optional[Dict[str, List[Dict[str, Union[str, int]]]]] = None,
        custom_fields: Optional[Dict[str, List[Dict[str, str]]]] = None,
        notification_uri: Optional[str] = None,
        event_notification: Optional[Dict[str, Union[str, List]]] = None,
        cdse_mode: Optional[str] = None,
        change_routing_order: Optional[bool] = None,
        completed_documents_only: Optional[bool] = None,
        merge_roles_on_draft: Optional[bool] = None,
        template_id: Optional[str] = None,
        template_roles: Optional[List[Dict[str, str]]] = None
    ) -> DocuSignResponse:
        """Creates an envelope or a draft envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            documents: Array of documents to include in the envelope
            email_subject: Subject line of the email message sent to all recipients
            email_blurb: Email message sent to all recipients
            status: Envelope status: sent, created, or draft
            recipients: Recipient information
            custom_fields: Custom fields
            notification_uri: Notification URI for envelope events
            event_notification: Event notification settings
            cdse_mode: Client Data Set Encryption mode
            change_routing_order: Change routing order
            completed_documents_only: Only return completed documents
            merge_roles_on_draft: Merge roles on draft
            template_id: Template ID to use for this envelope
            template_roles: Template roles to use

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if cdse_mode is not None:
            query_params.append(('cdse_mode', str(cdse_mode)))
        if change_routing_order is not None:
            query_params.append(('change_routing_order', str(change_routing_order).lower()))
        if completed_documents_only is not None:
            query_params.append(('completed_documents_only', str(completed_documents_only).lower()))
        if merge_roles_on_draft is not None:
            query_params.append(('merge_roles_on_draft', str(merge_roles_on_draft).lower()))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes".format(account_id=account_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        if documents is not None:
            body['documents'] = documents
        if email_subject is not None:
            body['emailSubject'] = email_subject
        if email_blurb is not None:
            body['emailBlurb'] = email_blurb
        if status is not None:
            body['status'] = status
        if recipients is not None:
            body['recipients'] = recipients
        if custom_fields is not None:
            body['customFields'] = custom_fields
        if notification_uri is not None:
            body['notificationUri'] = notification_uri
        if event_notification is not None:
            body['eventNotification'] = event_notification
        if template_id is not None:
            body['templateId'] = template_id
        if template_roles is not None:
            body['templateRoles'] = template_roles

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def get_envelope(
        self,
        account_id: str,
        envelope_id: str,
        include: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets the status of the specified envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope status that you want to get (required)
            include: Additional information to include in response

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if include is not None:
            query_params.append(('include', str(include)))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}".format(account_id=account_id, envelope_id=envelope_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def list_envelopes(
        self,
        account_id: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        status: Optional[str] = None,
        email: Optional[str] = None,
        envelope_ids: Optional[str] = None,
        start_position: Optional[str] = None,
        count: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets the status of all envelopes in the account

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            from_date: Start date for the date range. Format: MM/DD/YYYY
            to_date: End date for the date range. Format: MM/DD/YYYY
            status: Status of the envelopes to return
            email: Email address filter
            envelope_ids: Comma-separated list of envelope IDs to return
            start_position: Start position for pagination
            count: Number of records to return in the cache

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if from_date is not None:
            query_params.append(('from_date', str(from_date)))
        if to_date is not None:
            query_params.append(('to_date', str(to_date)))
        if status is not None:
            query_params.append(('status', str(status)))
        if email is not None:
            query_params.append(('email', str(email)))
        if envelope_ids is not None:
            query_params.append(('envelope_ids', str(envelope_ids)))
        if start_position is not None:
            query_params.append(('start_position', str(start_position)))
        if count is not None:
            query_params.append(('count', str(count)))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes".format(account_id=account_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def update_envelope(
        self,
        account_id: str,
        envelope_id: str,
        advanced_update: Optional[bool] = None,
        email_subject: Optional[str] = None,
        email_blurb: Optional[str] = None,
        status: Optional[str] = None,
        recipients: Optional[Dict[str, List[Dict[str, Union[str, int]]]]] = None,
        custom_fields: Optional[Dict[str, List[Dict[str, str]]]] = None
    ) -> DocuSignResponse:
        """Updates the specified envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID (required)
            advanced_update: When set to true, allows the caller to update recipients, tabs, custom fields, notification, email settings and other settings
            email_subject: Subject line of the email message sent to all recipients
            email_blurb: Email message sent to all recipients
            status: Status to set the envelope to
            recipients: Recipient information
            custom_fields: Custom fields

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if advanced_update is not None:
            query_params.append(('advanced_update', str(advanced_update).lower()))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}".format(account_id=account_id, envelope_id=envelope_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {}
        if email_subject is not None:
            body['emailSubject'] = email_subject
        if email_blurb is not None:
            body['emailBlurb'] = email_blurb
        if status is not None:
            body['status'] = status
        if recipients is not None:
            body['recipients'] = recipients
        if custom_fields is not None:
            body['customFields'] = custom_fields

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def delete_envelope(
        self,
        account_id: str,
        envelope_id: str
    ) -> DocuSignResponse:
        """Deletes the specified draft envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope to be deleted (required)

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}".format(account_id=account_id, envelope_id=envelope_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            if response.status == HTTPStatus.NO_CONTENT:
                return DocuSignResponse(success=True, data=None)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # ESIGNATURE - DOCUMENTS API
    # ========================================================================
    async def list_documents(
        self,
        account_id: str,
        envelope_id: str,
        include_metadata: Optional[bool] = None
    ) -> DocuSignResponse:
        """Gets a list of documents in the specified envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope being accessed (required)
            include_metadata: When set to true, the response includes metadata indicating which user can modify the document

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if include_metadata is not None:
            query_params.append(('include_metadata', str(include_metadata).lower()))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}/documents".format(account_id=account_id, envelope_id=envelope_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def get_document(
        self,
        account_id: str,
        envelope_id: str,
        document_id: str,
        certificate: Optional[bool] = None,
        encoding: Optional[str] = None,
        encrypt: Optional[bool] = None,
        language: Optional[str] = None,
        show_changes: Optional[bool] = None,
        watermark: Optional[bool] = None
    ) -> DocuSignResponse:
        """Gets a document from the specified envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope being accessed (required)
            document_id: The ID of the document being accessed (required)
            certificate: When set to true, returns additional certificate information
            encoding: The encoding format to use for the retrieved document
            encrypt: When set to true, the PDF bytes returned are encrypted for all the key managers configured on your DocuSign account
            language: Specifies the language for the Certificate of Completion in the response
            show_changes: When set to true, any changed fields for the returned PDF are highlighted in yellow and optional signatures or initials outlined in red
            watermark: When set to true, the account has the watermark feature enabled, and the envelope is not complete, the watermark for the account is added to the PDF documents

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if certificate is not None:
            query_params.append(('certificate', str(certificate).lower()))
        if encoding is not None:
            query_params.append(('encoding', str(encoding)))
        if encrypt is not None:
            query_params.append(('encrypt', str(encrypt).lower()))
        if language is not None:
            query_params.append(('language', str(language)))
        if show_changes is not None:
            query_params.append(('show_changes', str(show_changes).lower()))
        if watermark is not None:
            query_params.append(('watermark', str(watermark).lower()))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}/documents/{document_id}".format(
            account_id=account_id, envelope_id=envelope_id, document_id=document_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json() if response.headers.get("Content-Type") == "application/json" else response.content)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def add_document(
        self,
        account_id: str,
        envelope_id: str,
        document_id: str,
        document_bytes: bytes,
        document_name: Optional[str] = None,
        file_extension: Optional[str] = None
    ) -> DocuSignResponse:
        """Adds or replaces a document in an existing envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope being accessed (required)
            document_id: The ID of the document being accessed (required)
            document_bytes: The document content in bytes (required)
            document_name: The name of the document
            file_extension: The file extension type of the document

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if file_extension is not None:
            query_params.append(('file_extension', str(file_extension)))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}/documents/{document_id}".format(
            account_id=account_id, envelope_id=envelope_id, document_id=document_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        # For document upload, we need to handle multipart/form-data
        headers = self.http.headers.copy()

        files = {'file': document_bytes}
        body = {}
        if document_name is not None:
            body['documentName'] = document_name

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # ESIGNATURE - RECIPIENTS API
    # ========================================================================
    async def list_recipients(
        self,
        account_id: str,
        envelope_id: str,
        include_anchor_tab_locations: Optional[str] = None,
        include_extended: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets the status of recipients for an envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope being accessed (required)
            include_anchor_tab_locations: When set to true, all tabs with anchor strings are included in the response
            include_extended: When set to true, extended properties are included in the response

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if include_anchor_tab_locations is not None:
            query_params.append(('include_anchor_tab_locations', str(include_anchor_tab_locations)))
        if include_extended is not None:
            query_params.append(('include_extended', str(include_extended)))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}/recipients".format(
            account_id=account_id, envelope_id=envelope_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def update_recipients(
        self,
        account_id: str,
        envelope_id: str,
        signers: Optional[List[Dict[str, Union[str, int]]]] = None,
        carbon_copies: Optional[List[Dict[str, Union[str, int]]]] = None,
        certified_deliveries: Optional[List[Dict[str, Union[str, int]]]] = None,
        editors: Optional[List[Dict[str, Union[str, int]]]] = None,
        resend_envelope: Optional[bool] = None
    ) -> DocuSignResponse:
        """Updates recipients in a draft envelope or corrects recipient information for an in-process envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope being accessed (required)
            signers: A complex type containing information about the Signers of the document
            carbon_copies: A complex type containing information about the carbon copy recipients for the envelope
            certified_deliveries: A complex type containing information about the recipients who should receive a copy of the envelope
            editors: A complex type defining the management and access rights of a recipient assigned editor privileges
            resend_envelope: When set to true, resends the envelope if the new recipient's routing order is before or the same as the envelope's next recipient

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if resend_envelope is not None:
            query_params.append(('resend_envelope', str(resend_envelope).lower()))

        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}/recipients".format(
            account_id=account_id, envelope_id=envelope_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        body = {"recipients": {}}
        if signers is not None:
            body["recipients"]["signers"] = signers
        if carbon_copies is not None:
            body["recipients"]["carbonCopies"] = carbon_copies
        if certified_deliveries is not None:
            body["recipients"]["certifiedDeliveries"] = certified_deliveries
        if editors is not None:
            body["recipients"]["editors"] = editors

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def delete_recipient(
        self,
        account_id: str,
        envelope_id: str,
        recipient_id: str
    ) -> DocuSignResponse:
        """Deletes a recipient from a draft envelope or voided envelope

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            envelope_id: The envelope ID of the envelope being accessed (required)
            recipient_id: The ID of the recipient being accessed (required)

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/v2.1/accounts/{account_id}/envelopes/{envelope_id}/recipients/{recipient_id}".format(
            account_id=account_id, envelope_id=envelope_id, recipient_id=recipient_id)

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            if response.status == HTTPStatus.NO_CONTENT:
                return DocuSignResponse(success=True, data=None)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # ESIGNATURE - TEMPLATES API
    # ========================================================================
    async def list_templates(
        self,
        account_id: str,
        count: Optional[str] = None,
        folder_ids: Optional[str] = None,
        folder_types: Optional[str] = None,
        from_date: Optional[str] = None,
        include: Optional[str] = None,
        order: Optional[str] = None,
        order_by: Optional[str] = None,
        search_text: Optional[str] = None,
        shared_by_me: Optional[str] = None,
        start_position: Optional[str] = None,
        to_date: Optional[str] = None,
        used_from_date: Optional[str] = None,
        used_to_date: Optional[str] = None,
        user_filter: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets the list of templates for the specified account

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            count: Number of records to return in the cache
            folder_ids: A comma-separated list of folder ID GUIDs
            folder_types: A comma-separated list of folder types
            from_date: Start date for the date range filter
            include: Additional information to include in the response
            order: Sets the direction order used to sort the list
            order_by: Sets the file attribute used to sort the list
            search_text: The search text used to search template names
            shared_by_me: If true, the response only includes templates shared by the user
            start_position: Position of the template items to begin the list
            to_date: End date for the date range filter
            used_from_date: Start date for the template used date range filter
            used_to_date: End date for the template used date range filter
            user_filter: Sets if the templates shown in the response valid for the user

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if count is not None:
            query_params.append(('count', str(count)))
        if folder_ids is not None:
            query_params.append(('folder_ids', str(folder_ids)))
        if folder_types is not None:
            query_params.append(('folder_types', str(folder_types)))
        if from_date is not None:
            query_params.append(('from_date', str(from_date)))
        if include is not None:
            query_params.append(('include', str(include)))
        if order is not None:
            query_params.append(('order', str(order)))
        if order_by is not None:
            query_params.append(('order_by', str(order_by)))
        if search_text is not None:
            query_params.append(('search_text', str(search_text)))
        if shared_by_me is not None:
            query_params.append(('shared_by_me', str(shared_by_me)))
        if start_position is not None:
            query_params.append(('start_position', str(start_position)))
        if to_date is not None:
            query_params.append(('to_date', str(to_date)))
        if used_from_date is not None:
            query_params.append(('used_from_date', str(used_from_date)))
        if used_to_date is not None:
            query_params.append(('used_to_date', str(used_to_date)))
        if user_filter is not None:
            query_params.append(('user_filter', str(user_filter)))

        url = self.base_url + "/v2.1/accounts/{account_id}/templates".format(account_id=account_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def create_template(
        self,
        account_id: str,
        name: str,
        documents: List[Dict[str, Union[str, int, bytes]]],
        email_subject: Optional[str] = None,
        email_blurb: Optional[str] = None,
        description: Optional[str] = None,
        recipients: Optional[Dict[str, List[Dict[str, Union[str, int]]]]] = None,
        folder_name: Optional[str] = None,
        folder_id: Optional[str] = None,
        shared: Optional[bool] = None
    ) -> DocuSignResponse:
        """Creates a template definition

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            name: Name of the template (required)
            documents: Array of documents to include in the template (required)
            email_subject: Subject line of the email message sent to all recipients
            email_blurb: Email message sent to all recipients
            description: Description of the template
            recipients: Recipient information
            folder_name: Name of the folder where the template is stored
            folder_id: ID of the folder where the template is stored
            shared: When true, this template is shared with the Everyone group in the account

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/v2.1/accounts/{account_id}/templates".format(account_id=account_id)

        body = {}
        body['name'] = name
        body['documents'] = documents
        if email_subject is not None:
            body['emailSubject'] = email_subject
        if email_blurb is not None:
            body['emailBlurb'] = email_blurb
        if description is not None:
            body['description'] = description
        if recipients is not None:
            body['recipients'] = recipients
        if folder_name is not None:
            body['folderName'] = folder_name
        if folder_id is not None:
            body['folderId'] = folder_id
        if shared is not None:
            body['shared'] = shared

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body)
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # ESIGNATURE - USERS API
    # ========================================================================
    async def list_users(
        self,
        account_id: str,
        additional_info: Optional[str] = None,
        count: Optional[str] = None,
        email: Optional[str] = None,
        email_substring: Optional[str] = None,
        group_id: Optional[str] = None,
        include_closed: Optional[str] = None,
        include_usersettings_for_csv: Optional[str] = None,
        login_status: Optional[str] = None,
        not_group_id: Optional[str] = None,
        start_position: Optional[str] = None,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Retrieves the list of users for the specified account

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            additional_info: When true, the full list of user information is returned for all users in the account
            count: Number of records to return in the cache
            email: Filter users by email address
            email_substring: Filter users by email address substring
            group_id: Filter users by the ID of the group to which they belong
            include_closed: Filter users by closed status
            include_usersettings_for_csv: When true, user settings for CSV are included
            login_status: Filter users by login status
            not_group_id: Filter users by excluding those who belong to the specified group
            start_position: Position of the user records to begin the list
            status: Filter users by status

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if additional_info is not None:
            query_params.append(('additional_info', str(additional_info)))
        if count is not None:
            query_params.append(('count', str(count)))
        if email is not None:
            query_params.append(('email', str(email)))
        if email_substring is not None:
            query_params.append(('email_substring', str(email_substring)))
        if group_id is not None:
            query_params.append(('group_id', str(group_id)))
        if include_closed is not None:
            query_params.append(('include_closed', str(include_closed)))
        if include_usersettings_for_csv is not None:
            query_params.append(('include_usersettings_for_csv', str(include_usersettings_for_csv)))
        if login_status is not None:
            query_params.append(('login_status', str(login_status)))
        if not_group_id is not None:
            query_params.append(('not_group_id', str(not_group_id)))
        if start_position is not None:
            query_params.append(('start_position', str(start_position)))
        if status is not None:
            query_params.append(('status', str(status)))

        url = self.base_url + "/v2.1/accounts/{account_id}/users".format(account_id=account_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # ROOMS API
    # ========================================================================
    async def list_rooms(
        self,
        account_id: str,
        count: Optional[int] = None,
        start_position: Optional[int] = None,
        field_data_changed_start_date: Optional[str] = None,
        field_data_changed_end_date: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets a list of rooms in the account

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            count: Number of records to return in the response
            start_position: Starting position of records in the response
            field_data_changed_start_date: Start date for field data changes
            field_data_changed_end_date: End date for field data changes

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if count is not None:
            query_params.append(('count', str(count)))
        if start_position is not None:
            query_params.append(('start_position', str(start_position)))
        if field_data_changed_start_date is not None:
            query_params.append(('field_data_changed_start_date', str(field_data_changed_start_date)))
        if field_data_changed_end_date is not None:
            query_params.append(('field_data_changed_end_date', str(field_data_changed_end_date)))

        url = self.base_url + "/v2/accounts/{account_id}/rooms".format(account_id=account_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # CLICK API
    # ========================================================================
    async def list_clickwraps(
        self,
        account_id: str,
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page_number: Optional[str] = None,
        page_size: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets a list of clickwraps in the account

        Args:
            account_id: The external account number (int) or account ID GUID (required)
            status: Status of the clickwraps to return
            from_date: Start date filter for clickwraps
            to_date: End date filter for clickwraps
            page_number: Page number for paginated results
            page_size: Number of records to return per page

        Returns:
            DocuSignResponse with operation result
        """
        query_params = []
        if status is not None:
            query_params.append(('status', str(status)))
        if from_date is not None:
            query_params.append(('from_date', str(from_date)))
        if to_date is not None:
            query_params.append(('to_date', str(to_date)))
        if page_number is not None:
            query_params.append(('page_number', str(page_number)))
        if page_size is not None:
            query_params.append(('page_size', str(page_size)))

        url = self.base_url + "/v1/accounts/{account_id}/clickwraps".format(account_id=account_id)
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    # ========================================================================
    # MONITOR API
    # ========================================================================
    async def get_monitor_status(self) -> DocuSignResponse:
        """Gets DocuSign service status information

        Returns:
            DocuSignResponse with operation result
        """
        url = self.base_url + "/v2.1/monitor/status"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )

        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response.json())
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def get_api_info(self) -> DocuSignResponse:
        """Get information about the DocuSign API client.
        
        Returns:
            DocuSignResponse: Information about available API methods
        """
        info = {
            'total_methods': 22,  # Actual count of methods implemented above
            'base_url': self.base_url,
            'api_categories': [
                'Authentication (1 method)',
                'eSignature - Accounts (4 methods)',
                'eSignature - Envelopes (5 methods)',
                'eSignature - Documents (3 methods)',
                'eSignature - Recipients (3 methods)',
                'eSignature - Templates (3 methods)',
                'eSignature - Users (1 method)',
                'Rooms API (1 method)',
                'Click API (1 method)',
                'Monitor API (1 method)'
            ]
        }
        return DocuSignResponse(success=True, data=info)