# ruff: noqa
"""
OneTrust REST API DataSource - Auto-generated API wrapper

Generated from OneTrust REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: For OAuth clients, ensure_token() is called before each request
      to auto-fetch a client_credentials OAuth token.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.onetrust.onetrust import OneTrustClient, OneTrustResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class OneTrustDataSource:
    """OneTrust REST API DataSource

    Provides async wrapper methods for OneTrust REST API operations:
    - Data Subject Requests management
    - Privacy Notices management
    - Consent Receipts management
    - Assessments management
    - Data Inventory management
    - Risk Management
    - Vendor Management

    All methods return OneTrustResponse objects.
    """

    def __init__(self, client: OneTrustClient) -> None:
        """Initialize with OneTrustClient.

        Args:
            client: OneTrustClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'OneTrustDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> OneTrustClient:
        """Return the underlying OneTrustClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Data Subject Requests
    # -----------------------------------------------------------------------

    async def list_request_queues(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> OneTrustResponse:
        """List all data subject request queues

        HTTP GET /datasubject/v3/requestqueues

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/datasubject/v3/requestqueues"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_request_queues" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute list_request_queues")


    async def get_request_queue(
        self,
        request_id: str
    ) -> OneTrustResponse:
        """Get a specific data subject request queue by ID

        HTTP GET /datasubject/v3/requestqueues/{request_id}

        Args:
            request_id: The request id

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/datasubject/v3/requestqueues/{request_id}".format(request_id=request_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_request_queue" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute get_request_queue")


    # -----------------------------------------------------------------------
    # Privacy Notices
    # -----------------------------------------------------------------------

    async def list_privacy_notices(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> OneTrustResponse:
        """List all privacy notices

        HTTP GET /privacynotices/v3/notices

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/privacynotices/v3/notices"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_privacy_notices" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute list_privacy_notices")


    async def get_privacy_notice(
        self,
        notice_id: str
    ) -> OneTrustResponse:
        """Get a specific privacy notice by ID

        HTTP GET /privacynotices/v3/notices/{notice_id}

        Args:
            notice_id: The notice id

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/privacynotices/v3/notices/{notice_id}".format(notice_id=notice_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_privacy_notice" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute get_privacy_notice")


    # -----------------------------------------------------------------------
    # Consent Receipts
    # -----------------------------------------------------------------------

    async def list_consent_receipts(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> OneTrustResponse:
        """List all consent receipts

        HTTP GET /consent/v1/consentreceipts

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/consent/v1/consentreceipts"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_consent_receipts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute list_consent_receipts")


    async def get_consent_receipt(
        self,
        receipt_id: str
    ) -> OneTrustResponse:
        """Get a specific consent receipt by ID

        HTTP GET /consent/v1/consentreceipts/{receipt_id}

        Args:
            receipt_id: The receipt id

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/consent/v1/consentreceipts/{receipt_id}".format(receipt_id=receipt_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_consent_receipt" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute get_consent_receipt")


    # -----------------------------------------------------------------------
    # Assessments
    # -----------------------------------------------------------------------

    async def list_assessments(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> OneTrustResponse:
        """List all assessments

        HTTP GET /assessment/v2/assessments

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/assessment/v2/assessments"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_assessments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute list_assessments")


    async def get_assessment(
        self,
        assessment_id: str
    ) -> OneTrustResponse:
        """Get a specific assessment by ID

        HTTP GET /assessment/v2/assessments/{assessment_id}

        Args:
            assessment_id: The assessment id

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/assessment/v2/assessments/{assessment_id}".format(assessment_id=assessment_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_assessment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute get_assessment")


    # -----------------------------------------------------------------------
    # Data Inventory
    # -----------------------------------------------------------------------

    async def list_data_elements(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> OneTrustResponse:
        """List all data elements in the data inventory

        HTTP GET /dataInventory/v2/dataElements

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/dataInventory/v2/dataElements"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_data_elements" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute list_data_elements")


    # -----------------------------------------------------------------------
    # Risk Management
    # -----------------------------------------------------------------------

    async def list_risks(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> OneTrustResponse:
        """List all risks

        HTTP GET /riskmanagement/v2/risks

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/riskmanagement/v2/risks"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_risks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute list_risks")


    async def get_risk(
        self,
        risk_id: str
    ) -> OneTrustResponse:
        """Get a specific risk by ID

        HTTP GET /riskmanagement/v2/risks/{risk_id}

        Args:
            risk_id: The risk id

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/riskmanagement/v2/risks/{risk_id}".format(risk_id=risk_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_risk" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute get_risk")


    # -----------------------------------------------------------------------
    # Vendor Management
    # -----------------------------------------------------------------------

    async def list_vendors(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> OneTrustResponse:
        """List all vendors

        HTTP GET /vendormanagement/v2/vendors

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/vendormanagement/v2/vendors"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_vendors" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute list_vendors")


    async def get_vendor(
        self,
        vendor_id: str
    ) -> OneTrustResponse:
        """Get a specific vendor by ID

        HTTP GET /vendormanagement/v2/vendors/{vendor_id}

        Args:
            vendor_id: The vendor id

        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/vendormanagement/v2/vendors/{vendor_id}".format(vendor_id=vendor_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_vendor" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute get_vendor")

