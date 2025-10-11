from typing import Any, Dict, Optional

from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.servicenow.servicenow import (
    ServiceNowClient,
    ServiceNowResponse,
)


class ServiceNowDataSource:
    """
    Auto-generated data source for ServiceNow API operations.
    Base URL: https://<instance_name>.service-now.com
    This class combines all ServiceNow API endpoints from multiple OpenAPI specifications.
    """

    def __init__(self, client: ServiceNowClient) -> None:
        """
        Initialize the data source.
        Args:
            client: ServiceNow client instance
        """
        self.client = client.get_client()
        self.base_url = client.get_base_url()

    def _build_url(self, path: str) -> str:
        """Build full URL from path."""
        return f"{self.base_url}{path}"

    def _build_params(self, **kwargs) -> Dict[str, Any]:
        """Build query parameters, filtering out None values."""
        return {k: v for k, v in kwargs.items() if v is not None}

    async def _handle_response(self, response) -> ServiceNowResponse:
        """Handle API response and return ServiceNowResponse."""
        try:
            if response.status >= HttpStatusCode.BAD_REQUEST.value:
                return ServiceNowResponse(
                    success=False,
                    error=f"HTTP {response.status}",
                    message=response.text
                )

            data = response.json() if response.text else {}
            return ServiceNowResponse(
                success=True,
                data=data
            )
        except Exception as e:
            return ServiceNowResponse(
                success=False,
                error=str(e),
                message="Failed to parse response"
            )

    async def get_now_table_tableName(
        self,
        tableName: str,
        sysparm_query: Optional[str] = None,
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_suppress_pagination_header: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_view: Optional[str] = None,
        sysparm_query_category: Optional[str] = None,
        sysparm_query_no_domain: Optional[str] = None,
        sysparm_no_count: Optional[str] = None
    ) -> ServiceNowResponse:
        """Retrieve records from a table
        Args:
            tableName: Path parameter
            sysparm_query: An encoded query string used to filter the results
            sysparm_display_value: Return field display values (true), actual values (false), or both (all) (default: false)
            sysparm_exclude_reference_link: True to exclude Table API links for reference fields (default: false)
            sysparm_suppress_pagination_header: True to supress pagination header (default: false)
            sysparm_fields: A comma-separated list of fields to return in the response
            sysparm_limit: The maximum number of results returned per page (default: 10,000)
            sysparm_view: Render the response according to the specified UI view (overridden by sysparm_fields)
            sysparm_query_category: Name of the query category (read replica category) to use for queries
            sysparm_query_no_domain: True to access data across domains if authorized (default: false)
            sysparm_no_count: Do not execute a select count(*) on table (default: false)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/table/{tableName}")
        params = self._build_params(sysparm_query=sysparm_query, sysparm_display_value=sysparm_display_value, sysparm_exclude_reference_link=sysparm_exclude_reference_link, sysparm_suppress_pagination_header=sysparm_suppress_pagination_header, sysparm_fields=sysparm_fields, sysparm_limit=sysparm_limit, sysparm_view=sysparm_view, sysparm_query_category=sysparm_query_category, sysparm_query_no_domain=sysparm_query_no_domain, sysparm_no_count=sysparm_no_count)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.client.headers,
            query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_table_tableName(
        self,
        tableName: str,
        data: Dict[str, Any],
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_input_display_value: Optional[str] = None,
        sysparm_suppress_auto_sys_field: Optional[str] = None,
        sysparm_view: Optional[str] = None
    ) -> ServiceNowResponse:
        """Create a record
        Args:
            tableName: Path parameter
            data: Request body data
            sysparm_display_value: Return field display values (true), actual values (false), or both (all) (default: false)
            sysparm_exclude_reference_link: True to exclude Table API links for reference fields (default: false)
            sysparm_fields: A comma-separated list of fields to return in the response
            sysparm_input_display_value: Set field values using their display value (true) or actual value (false) (default: false)
            sysparm_suppress_auto_sys_field: True to suppress auto generation of system fields (default: false)
            sysparm_view: Render the response according to the specified UI view (overridden by sysparm_fields)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/table/{tableName}")
        params = self._build_params(sysparm_display_value=sysparm_display_value, sysparm_exclude_reference_link=sysparm_exclude_reference_link, sysparm_fields=sysparm_fields, sysparm_input_display_value=sysparm_input_display_value, sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field, sysparm_view=sysparm_view)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_table_tableName_sys_id(
        self,
        tableName: str,
        sys_id: str,
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_view: Optional[str] = None,
        sysparm_query_no_domain: Optional[str] = None
    ) -> ServiceNowResponse:
        """Retrieve a record
        Args:
            tableName: Path parameter
            sys_id: Path parameter
            sysparm_display_value: Return field display values (true), actual values (false), or both (all) (default: false)
            sysparm_exclude_reference_link: True to exclude Table API links for reference fields (default: false)
            sysparm_fields: A comma-separated list of fields to return in the response
            sysparm_view: Render the response according to the specified UI view (overridden by sysparm_fields)
            sysparm_query_no_domain: True to access data across domains if authorized (default: false)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/table/{tableName}/{sys_id}")
        params = self._build_params(sysparm_display_value=sysparm_display_value, sysparm_exclude_reference_link=sysparm_exclude_reference_link, sysparm_fields=sysparm_fields, sysparm_view=sysparm_view, sysparm_query_no_domain=sysparm_query_no_domain)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.client.headers,
            query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_now_table_tableName_sys_id(
        self,
        tableName: str,
        sys_id: str,
        data: Dict[str, Any],
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_input_display_value: Optional[str] = None,
        sysparm_suppress_auto_sys_field: Optional[str] = None,
        sysparm_view: Optional[str] = None,
        sysparm_query_no_domain: Optional[str] = None
    ) -> ServiceNowResponse:
        """Modify a record
        Args:
            tableName: Path parameter
            sys_id: Path parameter
            data: Request body data
            sysparm_display_value: Return field display values (true), actual values (false), or both (all) (default: false)
            sysparm_exclude_reference_link: True to exclude Table API links for reference fields (default: false)
            sysparm_fields: A comma-separated list of fields to return in the response
            sysparm_input_display_value: Set field values using their display value (true) or actual value (false) (default: false)
            sysparm_suppress_auto_sys_field: True to suppress auto generation of system fields (default: false)
            sysparm_view: Render the response according to the specified UI view (overridden by sysparm_fields)
            sysparm_query_no_domain: True to access data across domains if authorized (default: false)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/table/{tableName}/{sys_id}")
        params = self._build_params(sysparm_display_value=sysparm_display_value, sysparm_exclude_reference_link=sysparm_exclude_reference_link, sysparm_fields=sysparm_fields, sysparm_input_display_value=sysparm_input_display_value, sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field, sysparm_view=sysparm_view, sysparm_query_no_domain=sysparm_query_no_domain)

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_now_table_tableName_sys_id(
        self,
        tableName: str,
        sys_id: str,
        sysparm_query_no_domain: Optional[str] = None
    ) -> ServiceNowResponse:
        """Delete a record
        Args:
            tableName: Path parameter
            sys_id: Path parameter
            sysparm_query_no_domain: True to access data across domains if authorized (default: false)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/table/{tableName}/{sys_id}")
        params = self._build_params(sysparm_query_no_domain=sysparm_query_no_domain)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=self.client.headers,
            query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def patch_now_table_tableName_sys_id(
        self,
        tableName: str,
        sys_id: str,
        data: Dict[str, Any],
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_input_display_value: Optional[str] = None,
        sysparm_suppress_auto_sys_field: Optional[str] = None,
        sysparm_view: Optional[str] = None,
        sysparm_query_no_domain: Optional[str] = None
    ) -> ServiceNowResponse:
        """Update a record
        Args:
            tableName: Path parameter
            sys_id: Path parameter
            data: Request body data
            sysparm_display_value: Return field display values (true), actual values (false), or both (all) (default: false)
            sysparm_exclude_reference_link: True to exclude Table API links for reference fields (default: false)
            sysparm_fields: A comma-separated list of fields to return in the response
            sysparm_input_display_value: Set field values using their display value (true) or actual value (false) (default: false)
            sysparm_suppress_auto_sys_field: True to suppress auto generation of system fields (default: false)
            sysparm_view: Render the response according to the specified UI view (overridden by sysparm_fields)
            sysparm_query_no_domain: True to access data across domains if authorized (default: false)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/table/{tableName}/{sys_id}")
        params = self._build_params(sysparm_display_value=sysparm_display_value, sysparm_exclude_reference_link=sysparm_exclude_reference_link, sysparm_fields=sysparm_fields, sysparm_input_display_value=sysparm_input_display_value, sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field, sysparm_view=sysparm_view, sysparm_query_no_domain=sysparm_query_no_domain)

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_now_table_batch_api(
        self,
        data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/table_batch_api")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_table_batch_api(
        self,
        data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/table_batch_api")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_v1_email(
        self,
        data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Create a new email
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/email")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_v1_email_id(
        self,
        id: str,
        sysparm_fields: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get an email
        Args:
            id: Path parameter
            sysparm_fields: A comma-separated list of fields to return in the response
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/email/{id}")
        params = self._build_params(sysparm_fields=sysparm_fields)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=self.client.headers,
            query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)
