import json
from typing import Any, Dict, List, Optional

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignResponse
from app.sources.client.http.http_request import HTTPRequest


class DocuSignDataSource:
    """Comprehensive DocuSign API client wrapper.
    Provides async methods for ALL DocuSign API endpoints across:
    - eSignature API v2.1 (Accounts, Envelopes, Templates, Users, Groups)
    - Admin API v2.1 (Organizations, Users, Identity Providers)
    - Rooms API v2 (Real estate transactions, Users, Documents)
    - Click API v1 (Clickwrap agreements, Versions)
    - Maestro API v1 (Workflow orchestration)
    - WebForms API v1.1 (Form management)
    - Navigator API (Agreement analytics)
    - Monitor API v2 (Security events)
    All methods return DocuSignResponse objects with standardized format.
    Every parameter matches DocuSign's official API documentation exactly.
    """

    def __init__(self, client: DocuSignClient) -> None:
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'DocuSignDataSource':
        return self

    async def accounts_get_account(
        self,
        accountId: str,
        include_account_settings: Optional[bool] = None
    ) -> DocuSignResponse:
        """Retrieves the account information for a single account."""
        url = self.base_url + "/v2.1/accounts/{accountId}"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if include_account_settings is not None:
            params["include_account_settings"] = include_account_settings
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def accounts_create_account(
        self,
        accountName: str,
        initialUser: Dict[str, Any],
        preview_billing_plan: Optional[bool] = None,
        accountSettings: Optional[Dict[str, Any]] = None,
        addressInformation: Optional[Dict[str, Any]] = None,
        creditCardInformation: Optional[Dict[str, Any]] = None,
        distributorCode: Optional[str] = None,
        distributorPassword: Optional[str] = None,
        planInformation: Optional[Dict[str, Any]] = None,
        referralInformation: Optional[Dict[str, Any]] = None,
        socialAccountInformation: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Creates new DocuSign account."""
        url = self.base_url + "/v2.1/accounts"
        params = {}
        if preview_billing_plan is not None:
            params["preview_billing_plan"] = preview_billing_plan
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        body = {}
        if accountName is not None:
            body["accountName"] = accountName
        if accountSettings is not None:
            body["accountSettings"] = accountSettings
        if addressInformation is not None:
            body["addressInformation"] = addressInformation
        if creditCardInformation is not None:
            body["creditCardInformation"] = creditCardInformation
        if distributorCode is not None:
            body["distributorCode"] = distributorCode
        if distributorPassword is not None:
            body["distributorPassword"] = distributorPassword
        if initialUser is not None:
            body["initialUser"] = initialUser
        if planInformation is not None:
            body["planInformation"] = planInformation
        if referralInformation is not None:
            body["referralInformation"] = referralInformation
        if socialAccountInformation is not None:
            body["socialAccountInformation"] = socialAccountInformation
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def accounts_get_account_settings(
        self,
        accountId: str
    ) -> DocuSignResponse:
        """Gets account settings information."""
        url = self.base_url + "/v2.1/accounts/{accountId}/settings"
        url = url.replace("{accountId}", str(accountId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def accounts_update_account_settings(
        self,
        accountId: str,
        accountSettings: List[Dict[str, Any]]
    ) -> DocuSignResponse:
        """Updates the account settings for the specified account."""
        url = self.base_url + "/v2.1/accounts/{accountId}/settings"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if accountSettings is not None:
            body["accountSettings"] = accountSettings
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelopes_create_envelope(
        self,
        accountId: str,
        cdse_mode: Optional[str] = None,
        completed_documents_only: Optional[bool] = None,
        merge_roles_on_draft: Optional[bool] = None,
        allowMarkup: Optional[bool] = None,
        allowReassign: Optional[bool] = None,
        allowViewHistory: Optional[bool] = None,
        asynchronous: Optional[bool] = None,
        attachmentsUri: Optional[str] = None,
        authoritativeCopy: Optional[bool] = None,
        autoNavigation: Optional[bool] = None,
        brandId: Optional[str] = None,
        brandLock: Optional[bool] = None,
        burnDefaultTabData: Optional[bool] = None,
        certificateUri: Optional[str] = None,
        completedDateTime: Optional[str] = None,
        compositeTemplates: Optional[List[Dict[str, Any]]] = None,
        customFields: Optional[Dict[str, Any]] = None,
        customFieldsUri: Optional[str] = None,
        declinedDateTime: Optional[str] = None,
        deletedDateTime: Optional[str] = None,
        deliveredDateTime: Optional[str] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
        documentsUri: Optional[str] = None,
        emailBlurb: Optional[str] = None,
        emailSettings: Optional[Dict[str, Any]] = None,
        emailSubject: Optional[str] = None,
        enableWetSign: Optional[bool] = None,
        enforceSignerVisibility: Optional[bool] = None,
        envelopeAttachments: Optional[List[Dict[str, Any]]] = None,
        envelopeCustomMetadata: Optional[Dict[str, Any]] = None,
        envelopeDocuments: Optional[List[Dict[str, Any]]] = None,
        envelopeId: Optional[str] = None,
        envelopeIdStamping: Optional[bool] = None,
        envelopeLocation: Optional[str] = None,
        envelopeMetadata: Optional[Dict[str, Any]] = None,
        envelopeUri: Optional[str] = None,
        eventNotifications: Optional[List[Dict[str, Any]]] = None,
        expireAfter: Optional[int] = None,
        expireDateTime: Optional[str] = None,
        expireEnabled: Optional[bool] = None,
        externalEnvelopeId: Optional[str] = None,
        favoritedByMe: Optional[bool] = None,
        folderId: Optional[str] = None,
        folderIds: Optional[List[str]] = None,
        folderName: Optional[str] = None,
        folders: Optional[List[Dict[str, Any]]] = None,
        hasComments: Optional[bool] = None,
        hasFormDataChanged: Optional[bool] = None,
        hasWavFile: Optional[bool] = None,
        holder: Optional[str] = None,
        initialSentDateTime: Optional[str] = None,
        is21CFRPart11: Optional[bool] = None,
        isDynamicEnvelope: Optional[bool] = None,
        isSignatureProviderEnvelope: Optional[bool] = None,
        lastModifiedDateTime: Optional[str] = None,
        location: Optional[str] = None,
        lockInformation: Optional[Dict[str, Any]] = None,
        messageLock: Optional[bool] = None,
        notification: Optional[Dict[str, Any]] = None,
        notificationUri: Optional[str] = None,
        powerForm: Optional[Dict[str, Any]] = None,
        purgeCompletedDate: Optional[str] = None,
        purgeRequestDate: Optional[str] = None,
        purgeState: Optional[str] = None,
        recipients: Optional[Dict[str, Any]] = None,
        recipientsLock: Optional[bool] = None,
        recipientsUri: Optional[str] = None,
        recipientViewRequest: Optional[Dict[str, Any]] = None,
        sender: Optional[Dict[str, Any]] = None,
        sentDateTime: Optional[str] = None,
        signerCanSignOnMobile: Optional[bool] = None,
        signingLocation: Optional[str] = None,
        status: Optional[str] = None,
        statusChangedDateTime: Optional[str] = None,
        statusDateTime: Optional[str] = None,
        templatesUri: Optional[str] = None,
        transactionId: Optional[str] = None,
        useDisclosure: Optional[bool] = None,
        voidedDateTime: Optional[str] = None,
        voidedReason: Optional[str] = None,
        workflow: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Creates an envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if cdse_mode is not None:
            params["cdse_mode"] = cdse_mode
        if completed_documents_only is not None:
            params["completed_documents_only"] = completed_documents_only
        if merge_roles_on_draft is not None:
            params["merge_roles_on_draft"] = merge_roles_on_draft
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        body = {}
        if allowMarkup is not None:
            body["allowMarkup"] = allowMarkup
        if allowReassign is not None:
            body["allowReassign"] = allowReassign
        if allowViewHistory is not None:
            body["allowViewHistory"] = allowViewHistory
        if asynchronous is not None:
            body["asynchronous"] = asynchronous
        if attachmentsUri is not None:
            body["attachmentsUri"] = attachmentsUri
        if authoritativeCopy is not None:
            body["authoritativeCopy"] = authoritativeCopy
        if autoNavigation is not None:
            body["autoNavigation"] = autoNavigation
        if brandId is not None:
            body["brandId"] = brandId
        if brandLock is not None:
            body["brandLock"] = brandLock
        if burnDefaultTabData is not None:
            body["burnDefaultTabData"] = burnDefaultTabData
        if certificateUri is not None:
            body["certificateUri"] = certificateUri
        if completedDateTime is not None:
            body["completedDateTime"] = completedDateTime
        if compositeTemplates is not None:
            body["compositeTemplates"] = compositeTemplates
        if customFields is not None:
            body["customFields"] = customFields
        if customFieldsUri is not None:
            body["customFieldsUri"] = customFieldsUri
        if declinedDateTime is not None:
            body["declinedDateTime"] = declinedDateTime
        if deletedDateTime is not None:
            body["deletedDateTime"] = deletedDateTime
        if deliveredDateTime is not None:
            body["deliveredDateTime"] = deliveredDateTime
        if documents is not None:
            body["documents"] = documents
        if documentsUri is not None:
            body["documentsUri"] = documentsUri
        if emailBlurb is not None:
            body["emailBlurb"] = emailBlurb
        if emailSettings is not None:
            body["emailSettings"] = emailSettings
        if emailSubject is not None:
            body["emailSubject"] = emailSubject
        if enableWetSign is not None:
            body["enableWetSign"] = enableWetSign
        if enforceSignerVisibility is not None:
            body["enforceSignerVisibility"] = enforceSignerVisibility
        if envelopeAttachments is not None:
            body["envelopeAttachments"] = envelopeAttachments
        if envelopeCustomMetadata is not None:
            body["envelopeCustomMetadata"] = envelopeCustomMetadata
        if envelopeDocuments is not None:
            body["envelopeDocuments"] = envelopeDocuments
        if envelopeId is not None:
            body["envelopeId"] = envelopeId
        if envelopeIdStamping is not None:
            body["envelopeIdStamping"] = envelopeIdStamping
        if envelopeLocation is not None:
            body["envelopeLocation"] = envelopeLocation
        if envelopeMetadata is not None:
            body["envelopeMetadata"] = envelopeMetadata
        if envelopeUri is not None:
            body["envelopeUri"] = envelopeUri
        if eventNotifications is not None:
            body["eventNotifications"] = eventNotifications
        if expireAfter is not None:
            body["expireAfter"] = expireAfter
        if expireDateTime is not None:
            body["expireDateTime"] = expireDateTime
        if expireEnabled is not None:
            body["expireEnabled"] = expireEnabled
        if externalEnvelopeId is not None:
            body["externalEnvelopeId"] = externalEnvelopeId
        if favoritedByMe is not None:
            body["favoritedByMe"] = favoritedByMe
        if folderId is not None:
            body["folderId"] = folderId
        if folderIds is not None:
            body["folderIds"] = folderIds
        if folderName is not None:
            body["folderName"] = folderName
        if folders is not None:
            body["folders"] = folders
        if hasComments is not None:
            body["hasComments"] = hasComments
        if hasFormDataChanged is not None:
            body["hasFormDataChanged"] = hasFormDataChanged
        if hasWavFile is not None:
            body["hasWavFile"] = hasWavFile
        if holder is not None:
            body["holder"] = holder
        if initialSentDateTime is not None:
            body["initialSentDateTime"] = initialSentDateTime
        if is21CFRPart11 is not None:
            body["is21CFRPart11"] = is21CFRPart11
        if isDynamicEnvelope is not None:
            body["isDynamicEnvelope"] = isDynamicEnvelope
        if isSignatureProviderEnvelope is not None:
            body["isSignatureProviderEnvelope"] = isSignatureProviderEnvelope
        if lastModifiedDateTime is not None:
            body["lastModifiedDateTime"] = lastModifiedDateTime
        if location is not None:
            body["location"] = location
        if lockInformation is not None:
            body["lockInformation"] = lockInformation
        if messageLock is not None:
            body["messageLock"] = messageLock
        if notification is not None:
            body["notification"] = notification
        if notificationUri is not None:
            body["notificationUri"] = notificationUri
        if powerForm is not None:
            body["powerForm"] = powerForm
        if purgeCompletedDate is not None:
            body["purgeCompletedDate"] = purgeCompletedDate
        if purgeRequestDate is not None:
            body["purgeRequestDate"] = purgeRequestDate
        if purgeState is not None:
            body["purgeState"] = purgeState
        if recipients is not None:
            body["recipients"] = recipients
        if recipientsLock is not None:
            body["recipientsLock"] = recipientsLock
        if recipientsUri is not None:
            body["recipientsUri"] = recipientsUri
        if recipientViewRequest is not None:
            body["recipientViewRequest"] = recipientViewRequest
        if sender is not None:
            body["sender"] = sender
        if sentDateTime is not None:
            body["sentDateTime"] = sentDateTime
        if signerCanSignOnMobile is not None:
            body["signerCanSignOnMobile"] = signerCanSignOnMobile
        if signingLocation is not None:
            body["signingLocation"] = signingLocation
        if status is not None:
            body["status"] = status
        if statusChangedDateTime is not None:
            body["statusChangedDateTime"] = statusChangedDateTime
        if statusDateTime is not None:
            body["statusDateTime"] = statusDateTime
        if templatesUri is not None:
            body["templatesUri"] = templatesUri
        if transactionId is not None:
            body["transactionId"] = transactionId
        if useDisclosure is not None:
            body["useDisclosure"] = useDisclosure
        if voidedDateTime is not None:
            body["voidedDateTime"] = voidedDateTime
        if voidedReason is not None:
            body["voidedReason"] = voidedReason
        if workflow is not None:
            body["workflow"] = workflow
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelopes_get_envelope(
        self,
        accountId: str,
        envelopeId: str,
        advanced_update: Optional[bool] = None,
        include: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets the status of a single envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        params = {}
        if advanced_update is not None:
            params["advanced_update"] = advanced_update
        if include is not None:
            params["include"] = include
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelopes_update_envelope(
        self,
        accountId: str,
        envelopeId: str,
        advanced_update: Optional[bool] = None,
        resend_envelope: Optional[bool] = None,
        status: Optional[str] = None,
        voidedReason: Optional[str] = None,
        emailBlurb: Optional[str] = None,
        emailSubject: Optional[str] = None
    ) -> DocuSignResponse:
        """Send, void, or modify a draft envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        params = {}
        if advanced_update is not None:
            params["advanced_update"] = advanced_update
        if resend_envelope is not None:
            params["resend_envelope"] = resend_envelope
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        body = {}
        if status is not None:
            body["status"] = status
        if voidedReason is not None:
            body["voidedReason"] = voidedReason
        if emailBlurb is not None:
            body["emailBlurb"] = emailBlurb
        if emailSubject is not None:
            body["emailSubject"] = emailSubject
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelopes_list_envelopes(
        self,
        accountId: str,
        ac_status: Optional[str] = None,
        block: Optional[bool] = None,
        count: Optional[int] = None,
        custom_field: Optional[str] = None,
        email: Optional[str] = None,
        envelope_ids: Optional[str] = None,
        exclude: Optional[str] = None,
        folder_ids: Optional[str] = None,
        folder_types: Optional[str] = None,
        from_date: Optional[str] = None,
        from_to_status: Optional[str] = None,
        include: Optional[str] = None,
        include_purge_information: Optional[bool] = None,
        intersecting_folder_ids: Optional[str] = None,
        last_queried_date: Optional[str] = None,
        order: Optional[str] = None,
        order_by: Optional[str] = None,
        powerformids: Optional[str] = None,
        query_budget: Optional[str] = None,
        requester_date_format: Optional[str] = None,
        search_text: Optional[str] = None,
        start_position: Optional[int] = None,
        status: Optional[str] = None,
        to_date: Optional[str] = None,
        transaction_ids: Optional[str] = None,
        user_filter: Optional[str] = None,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets status changes for one or more envelopes."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if ac_status is not None:
            params["ac_status"] = ac_status
        if block is not None:
            params["block"] = block
        if count is not None:
            params["count"] = count
        if custom_field is not None:
            params["custom_field"] = custom_field
        if email is not None:
            params["email"] = email
        if envelope_ids is not None:
            params["envelope_ids"] = envelope_ids
        if exclude is not None:
            params["exclude"] = exclude
        if folder_ids is not None:
            params["folder_ids"] = folder_ids
        if folder_types is not None:
            params["folder_types"] = folder_types
        if from_date is not None:
            params["from_date"] = from_date
        if from_to_status is not None:
            params["from_to_status"] = from_to_status
        if include is not None:
            params["include"] = include
        if include_purge_information is not None:
            params["include_purge_information"] = include_purge_information
        if intersecting_folder_ids is not None:
            params["intersecting_folder_ids"] = intersecting_folder_ids
        if last_queried_date is not None:
            params["last_queried_date"] = last_queried_date
        if order is not None:
            params["order"] = order
        if order_by is not None:
            params["order_by"] = order_by
        if powerformids is not None:
            params["powerformids"] = powerformids
        if query_budget is not None:
            params["query_budget"] = query_budget
        if requester_date_format is not None:
            params["requester_date_format"] = requester_date_format
        if search_text is not None:
            params["search_text"] = search_text
        if start_position is not None:
            params["start_position"] = start_position
        if status is not None:
            params["status"] = status
        if to_date is not None:
            params["to_date"] = to_date
        if transaction_ids is not None:
            params["transaction_ids"] = transaction_ids
        if user_filter is not None:
            params["user_filter"] = user_filter
        if user_id is not None:
            params["user_id"] = user_id
        if user_name is not None:
            params["user_name"] = user_name
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelopes_delete_envelope(
        self,
        accountId: str,
        envelopeId: str
    ) -> DocuSignResponse:
        """Deletes a draft envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelope_recipients_list(
        self,
        accountId: str,
        envelopeId: str,
        include_anchor_tab_locations: Optional[bool] = None,
        include_extended: Optional[bool] = None,
        include_metadata: Optional[bool] = None,
        include_tabs: Optional[bool] = None
    ) -> DocuSignResponse:
        """Gets the status of recipients for an envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        params = {}
        if include_anchor_tab_locations is not None:
            params["include_anchor_tab_locations"] = include_anchor_tab_locations
        if include_extended is not None:
            params["include_extended"] = include_extended
        if include_metadata is not None:
            params["include_metadata"] = include_metadata
        if include_tabs is not None:
            params["include_tabs"] = include_tabs
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelope_recipients_create(
        self,
        accountId: str,
        envelopeId: str,
        resend_envelope: Optional[bool] = None,
        agents: Optional[List[Dict[str, Any]]] = None,
        carbonCopies: Optional[List[Dict[str, Any]]] = None,
        certifiedDeliveries: Optional[List[Dict[str, Any]]] = None,
        currentRoutingOrder: Optional[int] = None,
        editors: Optional[List[Dict[str, Any]]] = None,
        errorDetails: Optional[Dict[str, Any]] = None,
        inPersonSigners: Optional[List[Dict[str, Any]]] = None,
        intermediaries: Optional[List[Dict[str, Any]]] = None,
        notaries: Optional[List[Dict[str, Any]]] = None,
        recipientCount: Optional[int] = None,
        seals: Optional[List[Dict[str, Any]]] = None,
        signers: Optional[List[Dict[str, Any]]] = None,
        witnesses: Optional[List[Dict[str, Any]]] = None
    ) -> DocuSignResponse:
        """Adds one or more recipients to an envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        params = {}
        if resend_envelope is not None:
            params["resend_envelope"] = resend_envelope
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        body = {}
        if agents is not None:
            body["agents"] = agents
        if carbonCopies is not None:
            body["carbonCopies"] = carbonCopies
        if certifiedDeliveries is not None:
            body["certifiedDeliveries"] = certifiedDeliveries
        if currentRoutingOrder is not None:
            body["currentRoutingOrder"] = currentRoutingOrder
        if editors is not None:
            body["editors"] = editors
        if errorDetails is not None:
            body["errorDetails"] = errorDetails
        if inPersonSigners is not None:
            body["inPersonSigners"] = inPersonSigners
        if intermediaries is not None:
            body["intermediaries"] = intermediaries
        if notaries is not None:
            body["notaries"] = notaries
        if recipientCount is not None:
            body["recipientCount"] = recipientCount
        if seals is not None:
            body["seals"] = seals
        if signers is not None:
            body["signers"] = signers
        if witnesses is not None:
            body["witnesses"] = witnesses
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelope_recipients_update(
        self,
        accountId: str,
        envelopeId: str,
        combine_same_order_recipients: Optional[bool] = None,
        offline_signing: Optional[bool] = None,
        resend_envelope: Optional[bool] = None,
        agents: Optional[List[Dict[str, Any]]] = None,
        carbonCopies: Optional[List[Dict[str, Any]]] = None,
        certifiedDeliveries: Optional[List[Dict[str, Any]]] = None,
        currentRoutingOrder: Optional[int] = None,
        editors: Optional[List[Dict[str, Any]]] = None,
        errorDetails: Optional[Dict[str, Any]] = None,
        inPersonSigners: Optional[List[Dict[str, Any]]] = None,
        intermediaries: Optional[List[Dict[str, Any]]] = None,
        notaries: Optional[List[Dict[str, Any]]] = None,
        recipientCount: Optional[int] = None,
        seals: Optional[List[Dict[str, Any]]] = None,
        signers: Optional[List[Dict[str, Any]]] = None,
        witnesses: Optional[List[Dict[str, Any]]] = None
    ) -> DocuSignResponse:
        """Updates recipients in a draft envelope or corrects recipient information for an in-process envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        params = {}
        if combine_same_order_recipients is not None:
            params["combine_same_order_recipients"] = combine_same_order_recipients
        if offline_signing is not None:
            params["offline_signing"] = offline_signing
        if resend_envelope is not None:
            params["resend_envelope"] = resend_envelope
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        body = {}
        if agents is not None:
            body["agents"] = agents
        if carbonCopies is not None:
            body["carbonCopies"] = carbonCopies
        if certifiedDeliveries is not None:
            body["certifiedDeliveries"] = certifiedDeliveries
        if currentRoutingOrder is not None:
            body["currentRoutingOrder"] = currentRoutingOrder
        if editors is not None:
            body["editors"] = editors
        if errorDetails is not None:
            body["errorDetails"] = errorDetails
        if inPersonSigners is not None:
            body["inPersonSigners"] = inPersonSigners
        if intermediaries is not None:
            body["intermediaries"] = intermediaries
        if notaries is not None:
            body["notaries"] = notaries
        if recipientCount is not None:
            body["recipientCount"] = recipientCount
        if seals is not None:
            body["seals"] = seals
        if signers is not None:
            body["signers"] = signers
        if witnesses is not None:
            body["witnesses"] = witnesses
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelope_recipients_delete(
        self,
        accountId: str,
        envelopeId: str,
        recipientIds: List[str]
    ) -> DocuSignResponse:
        """Deletes recipients from a draft envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        body = {}
        if recipientIds is not None:
            body["recipientIds"] = recipientIds
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelope_documents_list(
        self,
        accountId: str,
        envelopeId: str,
        documents_by_userid: Optional[bool] = None,
        include_document_size: Optional[bool] = None
    ) -> DocuSignResponse:
        """Gets a list of envelope documents."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        params = {}
        if documents_by_userid is not None:
            params["documents_by_userid"] = documents_by_userid
        if include_document_size is not None:
            params["include_document_size"] = include_document_size
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelope_documents_get(
        self,
        accountId: str,
        envelopeId: str,
        documentId: str,
        certificate: Optional[bool] = None,
        documents_by_userid: Optional[bool] = None,
        encoding: Optional[str] = None,
        encrypt: Optional[bool] = None,
        language: Optional[str] = None,
        recipient_id: Optional[str] = None,
        shared_user_id: Optional[str] = None,
        show_changes: Optional[bool] = None,
        watermark: Optional[bool] = None
    ) -> DocuSignResponse:
        """Gets a document from an envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents/{documentId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        url = url.replace("{documentId}", str(documentId))
        params = {}
        if certificate is not None:
            params["certificate"] = certificate
        if documents_by_userid is not None:
            params["documents_by_userid"] = documents_by_userid
        if encoding is not None:
            params["encoding"] = encoding
        if encrypt is not None:
            params["encrypt"] = encrypt
        if language is not None:
            params["language"] = language
        if recipient_id is not None:
            params["recipient_id"] = recipient_id
        if shared_user_id is not None:
            params["shared_user_id"] = shared_user_id
        if show_changes is not None:
            params["show_changes"] = show_changes
        if watermark is not None:
            params["watermark"] = watermark
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/pdf"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def envelope_documents_put(
        self,
        accountId: str,
        envelopeId: str,
        documentId: str,
        document_base64: str,
        fileExtension: str,
        name: str
    ) -> DocuSignResponse:
        """Adds a document to a draft envelope."""
        url = self.base_url + "/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents/{documentId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{envelopeId}", str(envelopeId))
        url = url.replace("{documentId}", str(documentId))
        body = {}
        if document_base64 is not None:
            body["document_base64"] = document_base64
        if documentId is not None:
            body["documentId"] = documentId
        if fileExtension is not None:
            body["fileExtension"] = fileExtension
        if name is not None:
            body["name"] = name
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def templates_list(
        self,
        accountId: str,
        count: Optional[int] = None,
        created_from_date: Optional[str] = None,
        created_to_date: Optional[str] = None,
        folder_ids: Optional[str] = None,
        folder_types: Optional[str] = None,
        from_date: Optional[str] = None,
        include: Optional[str] = None,
        is_download: Optional[bool] = None,
        is_shared_by_me: Optional[bool] = None,
        modified_from_date: Optional[str] = None,
        modified_to_date: Optional[str] = None,
        order: Optional[str] = None,
        order_by: Optional[str] = None,
        search_fields: Optional[str] = None,
        search_text: Optional[str] = None,
        shared_by_me: Optional[bool] = None,
        start_position: Optional[int] = None,
        template_ids: Optional[str] = None,
        to_date: Optional[str] = None,
        used_from_date: Optional[str] = None,
        used_to_date: Optional[str] = None,
        user_filter: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets the definition of a template."""
        url = self.base_url + "/v2.1/accounts/{accountId}/templates"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if count is not None:
            params["count"] = count
        if created_from_date is not None:
            params["created_from_date"] = created_from_date
        if created_to_date is not None:
            params["created_to_date"] = created_to_date
        if folder_ids is not None:
            params["folder_ids"] = folder_ids
        if folder_types is not None:
            params["folder_types"] = folder_types
        if from_date is not None:
            params["from_date"] = from_date
        if include is not None:
            params["include"] = include
        if is_download is not None:
            params["is_download"] = is_download
        if is_shared_by_me is not None:
            params["is_shared_by_me"] = is_shared_by_me
        if modified_from_date is not None:
            params["modified_from_date"] = modified_from_date
        if modified_to_date is not None:
            params["modified_to_date"] = modified_to_date
        if order is not None:
            params["order"] = order
        if order_by is not None:
            params["order_by"] = order_by
        if search_fields is not None:
            params["search_fields"] = search_fields
        if search_text is not None:
            params["search_text"] = search_text
        if shared_by_me is not None:
            params["shared_by_me"] = shared_by_me
        if start_position is not None:
            params["start_position"] = start_position
        if template_ids is not None:
            params["template_ids"] = template_ids
        if to_date is not None:
            params["to_date"] = to_date
        if used_from_date is not None:
            params["used_from_date"] = used_from_date
        if used_to_date is not None:
            params["used_to_date"] = used_to_date
        if user_filter is not None:
            params["user_filter"] = user_filter
        if user_id is not None:
            params["user_id"] = user_id
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def templates_create(
        self,
        accountId: str,
        name: str,
        allowMarkup: Optional[bool] = None,
        allowReassign: Optional[bool] = None,
        allowViewHistory: Optional[bool] = None,
        asynchronous: Optional[bool] = None,
        authoritativeCopy: Optional[bool] = None,
        autoNavigation: Optional[bool] = None,
        brandId: Optional[str] = None,
        brandLock: Optional[bool] = None,
        burnDefaultTabData: Optional[bool] = None,
        created: Optional[str] = None,
        createdDateTime: Optional[str] = None,
        customFields: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
        emailBlurb: Optional[str] = None,
        emailSettings: Optional[Dict[str, Any]] = None,
        emailSubject: Optional[str] = None,
        enableWetSign: Optional[bool] = None,
        enforceSignerVisibility: Optional[bool] = None,
        envelopeCustomMetadata: Optional[Dict[str, Any]] = None,
        envelopeIdStamping: Optional[bool] = None,
        envelopeMetadata: Optional[Dict[str, Any]] = None,
        eventNotifications: Optional[List[Dict[str, Any]]] = None,
        folderId: Optional[str] = None,
        folderName: Optional[str] = None,
        folders: Optional[List[Dict[str, Any]]] = None,
        lastModified: Optional[str] = None,
        lastModifiedBy: Optional[Dict[str, Any]] = None,
        lastModifiedDateTime: Optional[str] = None,
        lastUsed: Optional[str] = None,
        messageLock: Optional[bool] = None,
        newPassword: Optional[str] = None,
        notification: Optional[Dict[str, Any]] = None,
        owner: Optional[Dict[str, Any]] = None,
        pageCount: Optional[int] = None,
        parentFolderId: Optional[str] = None,
        password: Optional[str] = None,
        recipients: Optional[Dict[str, Any]] = None,
        recipientsLock: Optional[bool] = None,
        shared: Optional[bool] = None,
        signerCanSignOnMobile: Optional[bool] = None,
        signingLocation: Optional[str] = None,
        templateId: Optional[str] = None,
        transactionId: Optional[str] = None,
        uri: Optional[str] = None,
        useDisclosure: Optional[bool] = None
    ) -> DocuSignResponse:
        """Creates an envelope from a template."""
        url = self.base_url + "/v2.1/accounts/{accountId}/templates"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if allowMarkup is not None:
            body["allowMarkup"] = allowMarkup
        if allowReassign is not None:
            body["allowReassign"] = allowReassign
        if allowViewHistory is not None:
            body["allowViewHistory"] = allowViewHistory
        if asynchronous is not None:
            body["asynchronous"] = asynchronous
        if authoritativeCopy is not None:
            body["authoritativeCopy"] = authoritativeCopy
        if autoNavigation is not None:
            body["autoNavigation"] = autoNavigation
        if brandId is not None:
            body["brandId"] = brandId
        if brandLock is not None:
            body["brandLock"] = brandLock
        if burnDefaultTabData is not None:
            body["burnDefaultTabData"] = burnDefaultTabData
        if created is not None:
            body["created"] = created
        if createdDateTime is not None:
            body["createdDateTime"] = createdDateTime
        if customFields is not None:
            body["customFields"] = customFields
        if description is not None:
            body["description"] = description
        if documents is not None:
            body["documents"] = documents
        if emailBlurb is not None:
            body["emailBlurb"] = emailBlurb
        if emailSettings is not None:
            body["emailSettings"] = emailSettings
        if emailSubject is not None:
            body["emailSubject"] = emailSubject
        if enableWetSign is not None:
            body["enableWetSign"] = enableWetSign
        if enforceSignerVisibility is not None:
            body["enforceSignerVisibility"] = enforceSignerVisibility
        if envelopeCustomMetadata is not None:
            body["envelopeCustomMetadata"] = envelopeCustomMetadata
        if envelopeIdStamping is not None:
            body["envelopeIdStamping"] = envelopeIdStamping
        if envelopeMetadata is not None:
            body["envelopeMetadata"] = envelopeMetadata
        if eventNotifications is not None:
            body["eventNotifications"] = eventNotifications
        if folderId is not None:
            body["folderId"] = folderId
        if folderName is not None:
            body["folderName"] = folderName
        if folders is not None:
            body["folders"] = folders
        if lastModified is not None:
            body["lastModified"] = lastModified
        if lastModifiedBy is not None:
            body["lastModifiedBy"] = lastModifiedBy
        if lastModifiedDateTime is not None:
            body["lastModifiedDateTime"] = lastModifiedDateTime
        if lastUsed is not None:
            body["lastUsed"] = lastUsed
        if messageLock is not None:
            body["messageLock"] = messageLock
        if name is not None:
            body["name"] = name
        if newPassword is not None:
            body["newPassword"] = newPassword
        if notification is not None:
            body["notification"] = notification
        if owner is not None:
            body["owner"] = owner
        if pageCount is not None:
            body["pageCount"] = pageCount
        if parentFolderId is not None:
            body["parentFolderId"] = parentFolderId
        if password is not None:
            body["password"] = password
        if recipients is not None:
            body["recipients"] = recipients
        if recipientsLock is not None:
            body["recipientsLock"] = recipientsLock
        if shared is not None:
            body["shared"] = shared
        if signerCanSignOnMobile is not None:
            body["signerCanSignOnMobile"] = signerCanSignOnMobile
        if signingLocation is not None:
            body["signingLocation"] = signingLocation
        if templateId is not None:
            body["templateId"] = templateId
        if transactionId is not None:
            body["transactionId"] = transactionId
        if uri is not None:
            body["uri"] = uri
        if useDisclosure is not None:
            body["useDisclosure"] = useDisclosure
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def templates_get(
        self,
        accountId: str,
        templateId: str,
        include: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets a specific template associated with a specified account."""
        url = self.base_url + "/v2.1/accounts/{accountId}/templates/{templateId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{templateId}", str(templateId))
        params = {}
        if include is not None:
            params["include"] = include
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def templates_update(
        self,
        accountId: str,
        templateId: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        shared: Optional[bool] = None,
        password: Optional[str] = None
    ) -> DocuSignResponse:
        """Updates an existing template."""
        url = self.base_url + "/v2.1/accounts/{accountId}/templates/{templateId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{templateId}", str(templateId))
        body = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if shared is not None:
            body["shared"] = shared
        if password is not None:
            body["password"] = password
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def templates_delete(
        self,
        accountId: str,
        templateId: str
    ) -> DocuSignResponse:
        """Deletes the specified template."""
        url = self.base_url + "/v2.1/accounts/{accountId}/templates/{templateId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{templateId}", str(templateId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def users_list(
        self,
        accountId: str,
        additional_info: Optional[bool] = None,
        alternate_admins_only: Optional[bool] = None,
        count: Optional[int] = None,
        domain_users_only: Optional[bool] = None,
        email: Optional[str] = None,
        email_substring: Optional[str] = None,
        group_id: Optional[str] = None,
        include_usersettings_for_csv: Optional[bool] = None,
        login_status: Optional[str] = None,
        not_group_id: Optional[str] = None,
        start_position: Optional[int] = None,
        status: Optional[str] = None,
        user_name_substring: Optional[str] = None
    ) -> DocuSignResponse:
        """Retrieves the list of users for the specified account."""
        url = self.base_url + "/v2.1/accounts/{accountId}/users"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if additional_info is not None:
            params["additional_info"] = additional_info
        if alternate_admins_only is not None:
            params["alternate_admins_only"] = alternate_admins_only
        if count is not None:
            params["count"] = count
        if domain_users_only is not None:
            params["domain_users_only"] = domain_users_only
        if email is not None:
            params["email"] = email
        if email_substring is not None:
            params["email_substring"] = email_substring
        if group_id is not None:
            params["group_id"] = group_id
        if include_usersettings_for_csv is not None:
            params["include_usersettings_for_csv"] = include_usersettings_for_csv
        if login_status is not None:
            params["login_status"] = login_status
        if not_group_id is not None:
            params["not_group_id"] = not_group_id
        if start_position is not None:
            params["start_position"] = start_position
        if status is not None:
            params["status"] = status
        if user_name_substring is not None:
            params["user_name_substring"] = user_name_substring
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def users_create(
        self,
        accountId: str,
        newUsers: List[Dict[str, Any]]
    ) -> DocuSignResponse:
        """Adds news user to the specified account."""
        url = self.base_url + "/v2.1/accounts/{accountId}/users"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if newUsers is not None:
            body["newUsers"] = newUsers
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def users_get(
        self,
        accountId: str,
        userId: str,
        additional_info: Optional[bool] = None
    ) -> DocuSignResponse:
        """Gets the user information for a specified user."""
        url = self.base_url + "/v2.1/accounts/{accountId}/users/{userId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{userId}", str(userId))
        params = {}
        if additional_info is not None:
            params["additional_info"] = additional_info
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def users_update(
        self,
        accountId: str,
        userId: str,
        allow_all_languages: Optional[bool] = None,
        activationAccessCode: Optional[str] = None,
        email: Optional[str] = None,
        enableConnectForUser: Optional[bool] = None,
        firstName: Optional[str] = None,
        forgottenPasswordInfo: Optional[Dict[str, Any]] = None,
        groupList: Optional[List[Dict[str, Any]]] = None,
        homeAddress: Optional[Dict[str, Any]] = None,
        initialsImageUri: Optional[str] = None,
        isAdmin: Optional[bool] = None,
        lastName: Optional[str] = None,
        loginStatus: Optional[str] = None,
        middleName: Optional[str] = None,
        password: Optional[str] = None,
        passwordExpiration: Optional[str] = None,
        profileImageUri: Optional[str] = None,
        sendActivationEmail: Optional[bool] = None,
        sendActivationOnInvalidLogin: Optional[bool] = None,
        signatureImageUri: Optional[str] = None,
        subscribe: Optional[bool] = None,
        suffixName: Optional[str] = None,
        title: Optional[str] = None,
        userName: Optional[str] = None,
        userSettings: Optional[List[Dict[str, Any]]] = None,
        workAddress: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Updates the user attributes of an existing account user."""
        url = self.base_url + "/v2.1/accounts/{accountId}/users/{userId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{userId}", str(userId))
        params = {}
        if allow_all_languages is not None:
            params["allow_all_languages"] = allow_all_languages
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        body = {}
        if activationAccessCode is not None:
            body["activationAccessCode"] = activationAccessCode
        if email is not None:
            body["email"] = email
        if enableConnectForUser is not None:
            body["enableConnectForUser"] = enableConnectForUser
        if firstName is not None:
            body["firstName"] = firstName
        if forgottenPasswordInfo is not None:
            body["forgottenPasswordInfo"] = forgottenPasswordInfo
        if groupList is not None:
            body["groupList"] = groupList
        if homeAddress is not None:
            body["homeAddress"] = homeAddress
        if initialsImageUri is not None:
            body["initialsImageUri"] = initialsImageUri
        if isAdmin is not None:
            body["isAdmin"] = isAdmin
        if lastName is not None:
            body["lastName"] = lastName
        if loginStatus is not None:
            body["loginStatus"] = loginStatus
        if middleName is not None:
            body["middleName"] = middleName
        if password is not None:
            body["password"] = password
        if passwordExpiration is not None:
            body["passwordExpiration"] = passwordExpiration
        if profileImageUri is not None:
            body["profileImageUri"] = profileImageUri
        if sendActivationEmail is not None:
            body["sendActivationEmail"] = sendActivationEmail
        if sendActivationOnInvalidLogin is not None:
            body["sendActivationOnInvalidLogin"] = sendActivationOnInvalidLogin
        if signatureImageUri is not None:
            body["signatureImageUri"] = signatureImageUri
        if subscribe is not None:
            body["subscribe"] = subscribe
        if suffixName is not None:
            body["suffixName"] = suffixName
        if title is not None:
            body["title"] = title
        if userName is not None:
            body["userName"] = userName
        if userSettings is not None:
            body["userSettings"] = userSettings
        if workAddress is not None:
            body["workAddress"] = workAddress
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def users_delete(
        self,
        accountId: str,
        userId: str
    ) -> DocuSignResponse:
        """Closes one or more user records."""
        url = self.base_url + "/v2.1/accounts/{accountId}/users/{userId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{userId}", str(userId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def groups_list(
        self,
        accountId: str,
        count: Optional[int] = None,
        group_type: Optional[str] = None,
        include_usercount: Optional[bool] = None,
        search_text: Optional[str] = None,
        start_position: Optional[int] = None
    ) -> DocuSignResponse:
        """Gets information about groups associated with the account."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if count is not None:
            params["count"] = count
        if group_type is not None:
            params["group_type"] = group_type
        if include_usercount is not None:
            params["include_usercount"] = include_usercount
        if search_text is not None:
            params["search_text"] = search_text
        if start_position is not None:
            params["start_position"] = start_position
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def groups_create(
        self,
        accountId: str,
        groups: List[Dict[str, Any]]
    ) -> DocuSignResponse:
        """Creates one or more groups for the account."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if groups is not None:
            body["groups"] = groups
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def groups_get(
        self,
        accountId: str,
        groupId: str
    ) -> DocuSignResponse:
        """Gets information about a group."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups/{groupId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{groupId}", str(groupId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def groups_update(
        self,
        accountId: str,
        groupId: str,
        groupName: Optional[str] = None,
        groupType: Optional[str] = None,
        permissionProfileId: Optional[str] = None
    ) -> DocuSignResponse:
        """Updates the group information for a group."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups/{groupId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{groupId}", str(groupId))
        body = {}
        if groupName is not None:
            body["groupName"] = groupName
        if groupType is not None:
            body["groupType"] = groupType
        if permissionProfileId is not None:
            body["permissionProfileId"] = permissionProfileId
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def groups_delete(
        self,
        accountId: str,
        groupId: str
    ) -> DocuSignResponse:
        """Deletes an existing user group."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups/{groupId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{groupId}", str(groupId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def group_users_list(
        self,
        accountId: str,
        groupId: str,
        count: Optional[int] = None,
        start_position: Optional[int] = None
    ) -> DocuSignResponse:
        """Gets a list of users in a group."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups/{groupId}/users"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{groupId}", str(groupId))
        params = {}
        if count is not None:
            params["count"] = count
        if start_position is not None:
            params["start_position"] = start_position
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def group_users_add(
        self,
        accountId: str,
        groupId: str,
        users: List[Dict[str, Any]]
    ) -> DocuSignResponse:
        """Adds one or more users to an existing group."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups/{groupId}/users"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{groupId}", str(groupId))
        body = {}
        if users is not None:
            body["users"] = users
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def group_users_delete(
        self,
        accountId: str,
        groupId: str,
        users: List[Dict[str, Any]]
    ) -> DocuSignResponse:
        """Deletes one or more users from a group."""
        url = self.base_url + "/v2.1/accounts/{accountId}/groups/{groupId}/users"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{groupId}", str(groupId))
        body = {}
        if users is not None:
            body["users"] = users
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organizations_list(
        self,
        mode: Optional[str] = None
    ) -> DocuSignResponse:
        """Returns the list of organizations that the authenticated user belongs to."""
        url = self.base_url + "/v2.1/organizations"
        params = {}
        if mode is not None:
            params["mode"] = mode
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organizations_get(
        self,
        organizationId: str
    ) -> DocuSignResponse:
        """Returns the details of an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}"
        url = url.replace("{organizationId}", str(organizationId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_accounts_list(
        self,
        organizationId: str,
        start_position: Optional[int] = None,
        count: Optional[int] = None
    ) -> DocuSignResponse:
        """Returns the list of accounts in an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/accounts"
        url = url.replace("{organizationId}", str(organizationId))
        params = {}
        if start_position is not None:
            params["start_position"] = start_position
        if count is not None:
            params["count"] = count
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_accounts_create(
        self,
        organizationId: str,
        accountName: str,
        accountSettings: Optional[List[Dict[str, Any]]] = None,
        addressInformation: Optional[Dict[str, Any]] = None,
        subscriptionDetails: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Creates a new account for an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/accounts"
        url = url.replace("{organizationId}", str(organizationId))
        body = {}
        if accountName is not None:
            body["accountName"] = accountName
        if accountSettings is not None:
            body["accountSettings"] = accountSettings
        if addressInformation is not None:
            body["addressInformation"] = addressInformation
        if subscriptionDetails is not None:
            body["subscriptionDetails"] = subscriptionDetails
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_users_list(
        self,
        organizationId: str,
        start_position: Optional[int] = None,
        count: Optional[int] = None,
        email: Optional[str] = None,
        email_substring: Optional[str] = None,
        status: Optional[str] = None,
        membership_status: Optional[str] = None
    ) -> DocuSignResponse:
        """Returns the list of users in an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/users"
        url = url.replace("{organizationId}", str(organizationId))
        params = {}
        if start_position is not None:
            params["start_position"] = start_position
        if count is not None:
            params["count"] = count
        if email is not None:
            params["email"] = email
        if email_substring is not None:
            params["email_substring"] = email_substring
        if status is not None:
            params["status"] = status
        if membership_status is not None:
            params["membership_status"] = membership_status
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_users_get(
        self,
        organizationId: str,
        userId: str
    ) -> DocuSignResponse:
        """Returns the details of an organization user."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/users/{userId}"
        url = url.replace("{organizationId}", str(organizationId))
        url = url.replace("{userId}", str(userId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_users_update(
        self,
        organizationId: str,
        userId: str,
        id: str,
        site_id: Optional[int] = None,
        user_name: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        email: Optional[str] = None,
        default_account_id: Optional[str] = None,
        language_culture: Optional[str] = None,
        selected_languages: Optional[List[str]] = None,
        fed_auth_required: Optional[str] = None,
        auto_activate_memberships: Optional[bool] = None
    ) -> DocuSignResponse:
        """Updates an organization user's details."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/users/{userId}"
        url = url.replace("{organizationId}", str(organizationId))
        url = url.replace("{userId}", str(userId))
        body = {}
        if id is not None:
            body["id"] = id
        if site_id is not None:
            body["site_id"] = site_id
        if user_name is not None:
            body["user_name"] = user_name
        if first_name is not None:
            body["first_name"] = first_name
        if last_name is not None:
            body["last_name"] = last_name
        if email is not None:
            body["email"] = email
        if default_account_id is not None:
            body["default_account_id"] = default_account_id
        if language_culture is not None:
            body["language_culture"] = language_culture
        if selected_languages is not None:
            body["selected_languages"] = selected_languages
        if fed_auth_required is not None:
            body["fed_auth_required"] = fed_auth_required
        if auto_activate_memberships is not None:
            body["auto_activate_memberships"] = auto_activate_memberships
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_users_delete(
        self,
        organizationId: str,
        userId: str,
        id: str
    ) -> DocuSignResponse:
        """Removes a user from an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/users/{userId}"
        url = url.replace("{organizationId}", str(organizationId))
        url = url.replace("{userId}", str(userId))
        body = {}
        if id is not None:
            body["id"] = id
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_user_imports_add(
        self,
        organizationId: str,
        file_csv: str
    ) -> DocuSignResponse:
        """Bulk adds users to an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/imports/bulk_users/add"
        url = url.replace("{organizationId}", str(organizationId))
        body = {}
        if file_csv is not None:
            body["file_csv"] = file_csv
        headers = self.http.headers.copy()
        headers["Content-Type"] = "multipart/form-data"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_user_imports_update(
        self,
        organizationId: str,
        file_csv: str
    ) -> DocuSignResponse:
        """Bulk updates users in an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/imports/bulk_users/update"
        url = url.replace("{organizationId}", str(organizationId))
        body = {}
        if file_csv is not None:
            body["file_csv"] = file_csv
        headers = self.http.headers.copy()
        headers["Content-Type"] = "multipart/form-data"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_organization_user_imports_close(
        self,
        organizationId: str,
        file_csv: str
    ) -> DocuSignResponse:
        """Bulk closes users in an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/imports/bulk_users/close"
        url = url.replace("{organizationId}", str(organizationId))
        body = {}
        if file_csv is not None:
            body["file_csv"] = file_csv
        headers = self.http.headers.copy()
        headers["Content-Type"] = "multipart/form-data"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_identity_providers_list(
        self,
        organizationId: str
    ) -> DocuSignResponse:
        """Returns the list of identity providers for an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/identity_providers"
        url = url.replace("{organizationId}", str(organizationId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def admin_reserved_domains_list(
        self,
        organizationId: str
    ) -> DocuSignResponse:
        """Returns the list of reserved domains for an organization."""
        url = self.base_url + "/v2.1/organizations/{organizationId}/reserved_domains"
        url = url.replace("{organizationId}", str(organizationId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def rooms_list(
        self,
        accountId: str,
        count: Optional[int] = None,
        start_position: Optional[int] = None,
        room_status: Optional[str] = None,
        office_id: Optional[int] = None
    ) -> DocuSignResponse:
        """Gets a list of rooms available to the user."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if count is not None:
            params["count"] = count
        if start_position is not None:
            params["start_position"] = start_position
        if room_status is not None:
            params["room_status"] = room_status
        if office_id is not None:
            params["office_id"] = office_id
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def rooms_create(
        self,
        accountId: str,
        name: str,
        templateId: Optional[int] = None,
        officeId: Optional[int] = None,
        fieldData: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Creates a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if name is not None:
            body["name"] = name
        if templateId is not None:
            body["templateId"] = templateId
        if officeId is not None:
            body["officeId"] = officeId
        if fieldData is not None:
            body["fieldData"] = fieldData
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def rooms_get(
        self,
        accountId: str,
        roomId: int,
        include: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets information about a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        params = {}
        if include is not None:
            params["include"] = include
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def rooms_update(
        self,
        accountId: str,
        roomId: int,
        name: Optional[str] = None,
        roomStatus: Optional[str] = None,
        fieldData: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Updates room details."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        body = {}
        if name is not None:
            body["name"] = name
        if roomStatus is not None:
            body["roomStatus"] = roomStatus
        if fieldData is not None:
            body["fieldData"] = fieldData
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def rooms_delete(
        self,
        accountId: str,
        roomId: int
    ) -> DocuSignResponse:
        """Deletes a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def room_users_list(
        self,
        accountId: str,
        roomId: int,
        count: Optional[int] = None,
        start_position: Optional[int] = None,
        filter: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets users in a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}/users"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        params = {}
        if count is not None:
            params["count"] = count
        if start_position is not None:
            params["start_position"] = start_position
        if filter is not None:
            params["filter"] = filter
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def room_users_invite(
        self,
        accountId: str,
        roomId: int,
        userId: int,
        email: str,
        firstName: str,
        lastName: str,
        accessLevel: Optional[str] = None,
        roleId: Optional[int] = None
    ) -> DocuSignResponse:
        """Invites a user to a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}/users"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        body = {}
        if userId is not None:
            body["userId"] = userId
        if email is not None:
            body["email"] = email
        if firstName is not None:
            body["firstName"] = firstName
        if lastName is not None:
            body["lastName"] = lastName
        if accessLevel is not None:
            body["accessLevel"] = accessLevel
        if roleId is not None:
            body["roleId"] = roleId
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def room_users_remove(
        self,
        accountId: str,
        roomId: int,
        userId: int
    ) -> DocuSignResponse:
        """Removes a user from a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}/users/{userId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        url = url.replace("{userId}", str(userId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def room_documents_list(
        self,
        accountId: str,
        roomId: int,
        count: Optional[int] = None,
        start_position: Optional[int] = None
    ) -> DocuSignResponse:
        """Gets a list of documents in a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}/documents"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        params = {}
        if count is not None:
            params["count"] = count
        if start_position is not None:
            params["start_position"] = start_position
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def room_documents_upload(
        self,
        accountId: str,
        roomId: int,
        file: str,
        documentData: Dict[str, Any]
    ) -> DocuSignResponse:
        """Uploads a document to a room."""
        url = self.base_url + "/v2/accounts/{accountId}/rooms/{roomId}/documents"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{roomId}", str(roomId))
        body = {}
        if file is not None:
            body["file"] = file
        if documentData is not None:
            body["documentData"] = documentData
        headers = self.http.headers.copy()
        headers["Content-Type"] = "multipart/form-data"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def room_templates_list(
        self,
        accountId: str,
        count: Optional[int] = None,
        start_position: Optional[int] = None,
        office_id: Optional[int] = None
    ) -> DocuSignResponse:
        """Gets room templates."""
        url = self.base_url + "/v2/accounts/{accountId}/room_templates"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if count is not None:
            params["count"] = count
        if start_position is not None:
            params["start_position"] = start_position
        if office_id is not None:
            params["office_id"] = office_id
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def room_templates_get(
        self,
        accountId: str,
        templateId: int
    ) -> DocuSignResponse:
        """Gets a room template."""
        url = self.base_url + "/v2/accounts/{accountId}/room_templates/{templateId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{templateId}", str(templateId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def rooms_company_users_list(
        self,
        accountId: str,
        count: Optional[int] = None,
        start_position: Optional[int] = None,
        email: Optional[str] = None,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets users in a company."""
        url = self.base_url + "/v2/accounts/{accountId}/users"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if count is not None:
            params["count"] = count
        if start_position is not None:
            params["start_position"] = start_position
        if email is not None:
            params["email"] = email
        if status is not None:
            params["status"] = status
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def rooms_company_users_invite(
        self,
        accountId: str,
        invitee: Dict[str, Any]
    ) -> DocuSignResponse:
        """Invites a user to join a company."""
        url = self.base_url + "/v2/accounts/{accountId}/users"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if invitee is not None:
            body["invitee"] = invitee
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwraps_list(
        self,
        accountId: str,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets a list of clickwraps."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if status is not None:
            params["status"] = status
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwraps_create(
        self,
        accountId: str,
        clickwrapName: str,
        displaySettings: Dict[str, Any],
        documents: List[Dict[str, Any]],
        fieldsSettings: Optional[Dict[str, Any]] = None,
        requireReacceptance: Optional[bool] = None,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Creates a clickwrap."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if clickwrapName is not None:
            body["clickwrapName"] = clickwrapName
        if displaySettings is not None:
            body["displaySettings"] = displaySettings
        if documents is not None:
            body["documents"] = documents
        if fieldsSettings is not None:
            body["fieldsSettings"] = fieldsSettings
        if requireReacceptance is not None:
            body["requireReacceptance"] = requireReacceptance
        if status is not None:
            body["status"] = status
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwraps_get(
        self,
        accountId: str,
        clickwrapId: str,
        versions: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets a specific clickwrap."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        params = {}
        if versions is not None:
            params["versions"] = versions
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwraps_update(
        self,
        accountId: str,
        clickwrapId: str,
        clickwrapName: Optional[str] = None,
        displaySettings: Optional[Dict[str, Any]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Updates a clickwrap."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        body = {}
        if clickwrapName is not None:
            body["clickwrapName"] = clickwrapName
        if displaySettings is not None:
            body["displaySettings"] = displaySettings
        if documents is not None:
            body["documents"] = documents
        if status is not None:
            body["status"] = status
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwraps_delete(
        self,
        accountId: str,
        clickwrapId: str
    ) -> DocuSignResponse:
        """Deletes a clickwrap."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwrap_versions_list(
        self,
        accountId: str,
        clickwrapId: str
    ) -> DocuSignResponse:
        """Gets clickwrap versions."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwrap_versions_create(
        self,
        accountId: str,
        clickwrapId: str,
        documents: List[Dict[str, Any]],
        displaySettings: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Creates a clickwrap version."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        body = {}
        if documents is not None:
            body["documents"] = documents
        if displaySettings is not None:
            body["displaySettings"] = displaySettings
        if status is not None:
            body["status"] = status
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwrap_versions_get(
        self,
        accountId: str,
        clickwrapId: str,
        versionId: str
    ) -> DocuSignResponse:
        """Gets a clickwrap version."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions/{versionId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        url = url.replace("{versionId}", str(versionId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwrap_versions_update(
        self,
        accountId: str,
        clickwrapId: str,
        versionId: str,
        documents: Optional[List[Dict[str, Any]]] = None,
        displaySettings: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Updates a clickwrap version."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}/versions/{versionId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        url = url.replace("{versionId}", str(versionId))
        body = {}
        if documents is not None:
            body["documents"] = documents
        if displaySettings is not None:
            body["displaySettings"] = displaySettings
        if status is not None:
            body["status"] = status
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwrap_agreements_list(
        self,
        accountId: str,
        clickwrapId: str,
        client_user_id: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        page_number: Optional[int] = None
    ) -> DocuSignResponse:
        """Gets clickwrap agreements."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}/agreements"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        params = {}
        if client_user_id is not None:
            params["client_user_id"] = client_user_id
        if from_date is not None:
            params["from_date"] = from_date
        if to_date is not None:
            params["to_date"] = to_date
        if page_number is not None:
            params["page_number"] = page_number
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def clickwrap_agreements_create(
        self,
        accountId: str,
        clickwrapId: str,
        clientUserId: str,
        documentData: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Creates a clickwrap agreement."""
        url = self.base_url + "/v1/accounts/{accountId}/clickwraps/{clickwrapId}/agreements"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{clickwrapId}", str(clickwrapId))
        body = {}
        if clientUserId is not None:
            body["clientUserId"] = clientUserId
        if documentData is not None:
            body["documentData"] = documentData
        if metadata is not None:
            body["metadata"] = metadata
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflows_list(
        self,
        accountId: str,
        status: Optional[str] = None,
        published: Optional[bool] = None
    ) -> DocuSignResponse:
        """Gets a list of workflows."""
        url = self.base_url + "/v1/accounts/{accountId}/workflows"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if status is not None:
            params["status"] = status
        if published is not None:
            params["published"] = published
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflows_create(
        self,
        accountId: str,
        workflowName: str,
        documentVersion: str,
        schemaVersion: str,
        participants: Dict[str, Any],
        trigger: Dict[str, Any],
        workflowDescription: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Creates a workflow."""
        url = self.base_url + "/v1/accounts/{accountId}/workflows"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if workflowName is not None:
            body["workflowName"] = workflowName
        if workflowDescription is not None:
            body["workflowDescription"] = workflowDescription
        if accountId is not None:
            body["accountId"] = accountId
        if documentVersion is not None:
            body["documentVersion"] = documentVersion
        if schemaVersion is not None:
            body["schemaVersion"] = schemaVersion
        if participants is not None:
            body["participants"] = participants
        if trigger is not None:
            body["trigger"] = trigger
        if variables is not None:
            body["variables"] = variables
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflows_get(
        self,
        accountId: str,
        workflowId: str
    ) -> DocuSignResponse:
        """Gets a workflow."""
        url = self.base_url + "/v1/accounts/{accountId}/workflows/{workflowId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{workflowId}", str(workflowId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflows_update(
        self,
        accountId: str,
        workflowId: str,
        workflowName: Optional[str] = None,
        workflowDescription: Optional[str] = None,
        participants: Optional[Dict[str, Any]] = None,
        trigger: Optional[Dict[str, Any]] = None,
        variables: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Updates a workflow."""
        url = self.base_url + "/v1/accounts/{accountId}/workflows/{workflowId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{workflowId}", str(workflowId))
        body = {}
        if workflowName is not None:
            body["workflowName"] = workflowName
        if workflowDescription is not None:
            body["workflowDescription"] = workflowDescription
        if participants is not None:
            body["participants"] = participants
        if trigger is not None:
            body["trigger"] = trigger
        if variables is not None:
            body["variables"] = variables
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflows_delete(
        self,
        accountId: str,
        workflowId: str
    ) -> DocuSignResponse:
        """Deletes a workflow."""
        url = self.base_url + "/v1/accounts/{accountId}/workflows/{workflowId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{workflowId}", str(workflowId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflow_instances_list(
        self,
        accountId: str,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets workflow instances."""
        url = self.base_url + "/v1/accounts/{accountId}/workflow_instances"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if workflow_id is not None:
            params["workflow_id"] = workflow_id
        if status is not None:
            params["status"] = status
        if from_date is not None:
            params["from_date"] = from_date
        if to_date is not None:
            params["to_date"] = to_date
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflow_instances_get(
        self,
        accountId: str,
        instanceId: str
    ) -> DocuSignResponse:
        """Gets a workflow instance."""
        url = self.base_url + "/v1/accounts/{accountId}/workflow_instances/{instanceId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{instanceId}", str(instanceId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def maestro_workflow_instances_cancel(
        self,
        accountId: str,
        instanceId: str,
        reason: Optional[str] = None
    ) -> DocuSignResponse:
        """Cancels a workflow instance."""
        url = self.base_url + "/v1/accounts/{accountId}/workflow_instances/{instanceId}/cancel"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{instanceId}", str(instanceId))
        body = {}
        if reason is not None:
            body["reason"] = reason
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webforms_list(
        self,
        accountId: str,
        search: Optional[str] = None,
        is_published: Optional[bool] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets a list of forms."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms"
        url = url.replace("{accountId}", str(accountId))
        params = {}
        if search is not None:
            params["search"] = search
        if is_published is not None:
            params["is_published"] = is_published
        if from_date is not None:
            params["from_date"] = from_date
        if to_date is not None:
            params["to_date"] = to_date
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webforms_create(
        self,
        accountId: str,
        formName: str,
        hasFile: bool,
        isStandAlone: bool,
        formMetadata: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Creates a form."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if formName is not None:
            body["formName"] = formName
        if hasFile is not None:
            body["hasFile"] = hasFile
        if isStandAlone is not None:
            body["isStandAlone"] = isStandAlone
        if formMetadata is not None:
            body["formMetadata"] = formMetadata
        if config is not None:
            body["config"] = config
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webforms_get(
        self,
        accountId: str,
        formId: str,
        state: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets a form."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms/{formId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{formId}", str(formId))
        params = {}
        if state is not None:
            params["state"] = state
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webforms_update(
        self,
        accountId: str,
        formId: str,
        formMetadata: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> DocuSignResponse:
        """Updates a form."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms/{formId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{formId}", str(formId))
        body = {}
        if formMetadata is not None:
            body["formMetadata"] = formMetadata
        if config is not None:
            body["config"] = config
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webforms_delete(
        self,
        accountId: str,
        formId: str
    ) -> DocuSignResponse:
        """Deletes a form."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms/{formId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{formId}", str(formId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webform_instances_list(
        self,
        accountId: str,
        formId: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        status: Optional[str] = None
    ) -> DocuSignResponse:
        """Gets form instances."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms/{formId}/instances"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{formId}", str(formId))
        params = {}
        if from_date is not None:
            params["from_date"] = from_date
        if to_date is not None:
            params["to_date"] = to_date
        if status is not None:
            params["status"] = status
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webform_instances_get(
        self,
        accountId: str,
        formId: str,
        instanceId: str
    ) -> DocuSignResponse:
        """Gets a form instance."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms/{formId}/instances/{instanceId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{formId}", str(formId))
        url = url.replace("{instanceId}", str(instanceId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def webform_instances_refresh(
        self,
        accountId: str,
        formId: str,
        instanceId: str
    ) -> DocuSignResponse:
        """Refreshes a form instance."""
        url = self.base_url + "/v1.1/accounts/{accountId}/forms/{formId}/instances/{instanceId}/refresh"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{formId}", str(formId))
        url = url.replace("{instanceId}", str(instanceId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def navigator_data_sources_list(
        self,
        accountId: str
    ) -> DocuSignResponse:
        """Gets data sources."""
        url = self.base_url + "/v1/accounts/{accountId}/data_sources"
        url = url.replace("{accountId}", str(accountId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def navigator_data_sources_create(
        self,
        accountId: str,
        name: str,
        type: str,
        configuration: Dict[str, Any],
        description: Optional[str] = None
    ) -> DocuSignResponse:
        """Creates a data source."""
        url = self.base_url + "/v1/accounts/{accountId}/data_sources"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if name is not None:
            body["name"] = name
        if type is not None:
            body["type"] = type
        if configuration is not None:
            body["configuration"] = configuration
        if description is not None:
            body["description"] = description
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def navigator_data_sources_get(
        self,
        accountId: str,
        dataSourceId: str
    ) -> DocuSignResponse:
        """Gets a data source."""
        url = self.base_url + "/v1/accounts/{accountId}/data_sources/{dataSourceId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{dataSourceId}", str(dataSourceId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def navigator_data_sources_update(
        self,
        accountId: str,
        dataSourceId: str,
        name: Optional[str] = None,
        configuration: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None
    ) -> DocuSignResponse:
        """Updates a data source."""
        url = self.base_url + "/v1/accounts/{accountId}/data_sources/{dataSourceId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{dataSourceId}", str(dataSourceId))
        body = {}
        if name is not None:
            body["name"] = name
        if configuration is not None:
            body["configuration"] = configuration
        if description is not None:
            body["description"] = description
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def navigator_data_sources_delete(
        self,
        accountId: str,
        dataSourceId: str
    ) -> DocuSignResponse:
        """Deletes a data source."""
        url = self.base_url + "/v1/accounts/{accountId}/data_sources/{dataSourceId}"
        url = url.replace("{accountId}", str(accountId))
        url = url.replace("{dataSourceId}", str(dataSourceId))
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def navigator_queries_execute(
        self,
        accountId: str,
        query: str,
        dataSourceIds: List[str],
        maxResults: Optional[int] = None
    ) -> DocuSignResponse:
        """Executes a query."""
        url = self.base_url + "/v1/accounts/{accountId}/queries"
        url = url.replace("{accountId}", str(accountId))
        body = {}
        if query is not None:
            body["query"] = query
        if dataSourceIds is not None:
            body["dataSourceIds"] = dataSourceIds
        if maxResults is not None:
            body["maxResults"] = maxResults
        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body) if body else None
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))

    async def monitor_dataset_stream(
        self,
        version: str,
        dataSetName: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None
    ) -> DocuSignResponse:
        """Gets customer event data for an organization."""
        url = self.base_url + "/api/v{version}/datasets/{dataSetName}/stream"
        url = url.replace("{version}", str(version))
        url = url.replace("{dataSetName}", str(dataSetName))
        params = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        headers = self.http.headers.copy()
        headers["Accept"] = "application/json"
        headers["Authorization"] = self._client.get_auth_header()
        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers
        )
        try:
            response = await self.http.execute(request)
            return DocuSignResponse(success=True, data=response)
        except Exception as e:
            return DocuSignResponse(success=False, error=str(e))
