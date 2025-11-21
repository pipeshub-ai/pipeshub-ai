from typing import Any, Dict, List, Optional

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
                    message=response.text(),
                )

            # Handle 204 No Content (successful delete operations)
            response_text = response.text()
            if response.status == HttpStatusCode.NO_CONTENT or not response_text:
                return ServiceNowResponse(
                    success=True,
                    data={"message": "Operation completed successfully"},
                    message=(
                        "Record deleted successfully"
                        if response.status == HttpStatusCode.NO_CONTENT
                        else None
                    ),
                )

            data = response.json() if response_text else {}
            return ServiceNowResponse(success=True, data=data)
        except Exception as e:
            return ServiceNowResponse(
                success=False, error=str(e), message="Failed to parse response"
            )

    async def get_ace_fetchClientScripts_acePageId_pageId(
        self, acePageId: str, pageId: str
    ) -> ServiceNowResponse:
        """
        Args:
            acePageId: Path parameter
            pageId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ace/fetchClientScripts/{acePageId}/{pageId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_fetchComponentContentTypeProps(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/fetchComponentContentTypeProps")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_fetchControlProps(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/fetchControlProps")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_fetchReusableBlockContentTree(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/fetchReusableBlockContentTree")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ace_fetchRoutes(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/fetchRoutes")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_fetch_translation(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/fetch_translation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_fetchdata(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/fetchdata")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_fetchmeta(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/fetchmeta")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ace_getFlowActionData(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/getFlowActionData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_refresh_block(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/refresh_block")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_reusableblock(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/reusableblock")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_setControl(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/setControl")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_updateClientContext(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/updateClientContext")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_updateClientScripts(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/updateClientScripts")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_updateContentBlockContext(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/updateContentBlockContext")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ace_updatemetadata(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ace/updatemetadata")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_activities(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/actsub/activities")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_contexts(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/actsub/contexts")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_facets_activity_context_context_instance(
        self, activity_context: str, context_instance: str
    ) -> ServiceNowResponse:
        """
        Args:
            activity_context: Path parameter
            context_instance: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/facets/{activity_context}/{context_instance}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_followings_follower(self, follower: str) -> ServiceNowResponse:
        """
        Args:
            follower: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/followings/{follower}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_actsub_preferences(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/actsub/preferences")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_preferences_profileId(
        self, profileId: str
    ) -> ServiceNowResponse:
        """
        Args:
            profileId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/preferences/{profileId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_subobjects(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/actsub/subobjects")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_subscribers_sub_object(
        self, sub_object: str
    ) -> ServiceNowResponse:
        """
        Args:
            sub_object: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/subscribers/{sub_object}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_subscriptions_subscriber_id(
        self, subscriber_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            subscriber_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/subscriptions/{subscriber_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_subscriptions_isSubscribed_sub_obj_id(
        self, sub_obj_id: str
    ) -> ServiceNowResponse:
        """Returns if current user is subscribed to the object
        Args:
            sub_obj_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/subscriptions/{sub_obj_id}/isSubscribed")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_subscriptions_subscribe_sub_obj_Id(
        self, sub_obj_Id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """This is to subscribe to the subscribable object
        Args:
            sub_obj_Id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/subscriptions/{sub_obj_Id}/subscribe")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_subscriptions_unsubscribe_sub_obj_id(
        self, sub_obj_id: str
    ) -> ServiceNowResponse:
        """Remove subscription for the current user for the given object
        Args:
            sub_obj_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/subscriptions/{sub_obj_id}/unsubscribe")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_actsub_userstream_profileId(
        self, profileId: str
    ) -> ServiceNowResponse:
        """
        Args:
            profileId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/userstream/{profileId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_actsub_userstream_profileId(
        self, profileId: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            profileId: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/actsub/userstream/{profileId}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_advance_chat_settings_get_feature_status(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/advance_chat_settings/get_feature_status")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_agent_initiated_message_channel_specific_validation(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/agent_initiated_message/channel_specific_validation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_agent_initiated_message_get_application_provider(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/agent_initiated_message/get_application_provider")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_agent_initiated_message_send_message(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/agent_initiated_message/send_message")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_stats_tableName(
        self,
        tableName: str,
        sysparm_query: Optional[str] = None,
        sysparm_avg_fields: Optional[str] = None,
        sysparm_count: Optional[str] = None,
        sysparm_min_fields: Optional[str] = None,
        sysparm_max_fields: Optional[str] = None,
        sysparm_sum_fields: Optional[str] = None,
        sysparm_group_by: Optional[str] = None,
        sysparm_order_by: Optional[str] = None,
        sysparm_having: Optional[str] = None,
        sysparm_display_value: Optional[str] = None,
        sysparm_query_category: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Retrieve statistical calculations for a table
        Args:
            tableName: Path parameter
            sysparm_query: An encoded query string
            sysparm_avg_fields: A comma-separated list of fields for which to calculate the average value
            sysparm_count: Calculate the number of records (default: false)
            sysparm_min_fields: A comma-separated list of fields for which to calculate the minimum value
            sysparm_max_fields: A comma-separated list of fields for which to calculate the maximum value
            sysparm_sum_fields: A comma-separated list of fields for which to calculate the sum of the values
            sysparm_group_by: A comma-separated list of fields to group results by
            sysparm_order_by: A comma-separated list of fields to order results by
            sysparm_having: An additional query allowing you to filter the data based on an aggregate operation
            sysparm_display_value: Return the display value (true), actual value (false), or both (all) in grouped results (default: false)
            sysparm_query_category: Name of the query category (read replica category) to use for queries
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/stats/{tableName}")
        params = self._build_params(
            sysparm_query=sysparm_query,
            sysparm_avg_fields=sysparm_avg_fields,
            sysparm_count=sysparm_count,
            sysparm_min_fields=sysparm_min_fields,
            sysparm_max_fields=sysparm_max_fields,
            sysparm_sum_fields=sysparm_sum_fields,
            sysparm_group_by=sysparm_group_by,
            sysparm_order_by=sysparm_order_by,
            sysparm_having=sysparm_having,
            sysparm_display_value=sysparm_display_value,
            sysparm_query_category=sysparm_query_category,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_aisa_search(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/aisa/search")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_aisa_action_resolves(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/aisa_action/resolves")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_aisa_action_sc_cat_item_order(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/aisa_action/sc_cat_item_order")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_addBaseline(self) -> ServiceNowResponse:
        """Adds new baseline to history timeline of given service
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/addBaseline")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_bsData(self) -> ServiceNowResponse:
        """Get proeprteis of service for current/history map view.
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/bsData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_ciFullData(self) -> ServiceNowResponse:
        """Get properties for CI in current/historical map view
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/ciFullData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_compareBsData(self) -> ServiceNowResponse:
        """Service properties for comparison map.
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/compareBsData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_compareCiFullData(self) -> ServiceNowResponse:
        """Ci properties for comparison map.
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/compareCiFullData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_it_service_compareEdgesData(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Edges properties for comparison map.
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/compareEdgesData")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_compareService(self) -> ServiceNowResponse:
        """Get comparison map for service (map json).
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/compareService")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_it_service_edgesData(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/edgesData")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_getAllBSPreferences(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/getAllBSPreferences")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_getBSPreference(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/getBSPreference")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_getBaselines(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/getBaselines")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_it_service_getParentServices(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/getParentServices")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_getServiceTimeline(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/getServiceTimeline")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_getServiceTimelineCheckpoints(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/getServiceTimelineCheckpoints")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_isChanged(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/isChanged")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_map(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/map")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_mapNotifications(self) -> ServiceNowResponse:
        """Get map notifications for UI. Currently supports single notification at time
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/mapNotifications")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_map_api(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/map_api")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_processDataFromGenericAppl(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/processDataFromGenericAppl")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_relatedItemsFullData(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/relatedItemsFullData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_removeBaseline(self) -> ServiceNowResponse:
        """Removes baseline from history timeline, by baseline sys_id.
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/removeBaseline")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_serviceTreeData(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/serviceTreeData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_setBSPreference(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/setBSPreference")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_updateBaseline(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/updateBaseline")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_it_service_wasTableUpdatedSince(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/it_service/wasTableUpdatedSince")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_app_service_convertToDynamicService(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb/app_service/convertToDynamicService")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_app_service_convertToManualService(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb/app_service/convertToManualService")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_app_service_create(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Create or update an application service, including connections between CIs
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb/app_service/create")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_app_service_createDynamicService(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb/app_service/createDynamicService")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_app_service_updateDynamicNumberOfLevels(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb/app_service/updateDynamicNumberOfLevels")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_app_service_getContent_sys_id(
        self, sys_id: str, mode: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get the content of an application service including connections between CIs
        Args:
            sys_id: Path parameter
            mode: get minimum or full details of each CI (shallow or full)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/app_service/{sys_id}/getContent")
        params = self._build_params(mode=mode)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_app_service_find_service(
        self, name: Optional[str] = None, number: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            name: Criteria for finding Application Services
            number: Numer field on Application Service
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb/csdm/app_service/find_service")
        params = self._build_params(name=name, number=number)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_app_service_register_service(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb/csdm/app_service/register_service")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_app_service_populate_service_service_sys_id(
        self, service_sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            service_sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/cmdb/csdm/app_service/{service_sys_id}/populate_service"
        )
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_app_service_service_details_service_sys_id(
        self, service_sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            service_sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/cmdb/csdm/app_service/{service_sys_id}/service_details"
        )
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_service_mapping_getMapNavigationUrl_service_counters_id(
        self, service_counters_id: str
    ) -> ServiceNowResponse:
        """Get url of service map by sys_id in sa_service_counters
        Args:
            service_counters_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/service_mapping/getMapNavigationUrl/{service_counters_id}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_atf_agent_online(
        self, id: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            id: ID of the sys_atf_agent to check
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/atf_agent/online")
        params = self._build_params(id=id)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_attachment(
        self,
        sysparm_query: Optional[str] = None,
        sysparm_suppress_pagination_header: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_query_category: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Retrieve metadata for attachments
        Args:
            sysparm_query: An encoded query string used to filter the results (relative to Attachment table)
            sysparm_suppress_pagination_header: True to supress pagination header (default: false)
            sysparm_limit: The maximum number of results returned per page (default: 10,000)
            sysparm_query_category: Name of the query category (read replica category) to use for queries
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/attachment")
        params = self._build_params(
            sysparm_query=sysparm_query,
            sysparm_suppress_pagination_header=sysparm_suppress_pagination_header,
            sysparm_limit=sysparm_limit,
            sysparm_query_category=sysparm_query_category,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_attachment_file(
        self,
        data: Dict[str, Any],
        table_name: str,
        table_sys_id: str,
        file_name: str,
        encryption_context: Optional[str] = None,
        creation_time: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Upload an attachment from a binary request
        Args:
            data: Request body data
            table_name: Table to attach the file to
            table_sys_id: Record to attach the file to
            file_name: File name for the attachment
            encryption_context: Encryption Context or Crypto Module to be used if file to be saved encrypted
            creation_time: Custom creation time for the attachment
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/attachment/file")
        params = self._build_params(
            table_name=table_name,
            table_sys_id=table_sys_id,
            file_name=file_name,
            encryption_context=encryption_context,
            creation_time=creation_time,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_attachment_upload(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Upload an attachment from a multipart form
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/attachment/upload")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_attachment_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """Retrieve attachment metadata
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/attachment/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_now_attachment_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """Delete an attachment
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/attachment/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_attachment_file_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """Retrieve attachment content
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/attachment/{sys_id}/file")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_attachment_file_table_sys_id_file_name(
        self, table_sys_id: str, file_name: str
    ) -> ServiceNowResponse:
        """Retrieve first attachment content by file name
        Args:
            table_sys_id: Path parameter
            file_name: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/attachment/{table_sys_id}/{file_name}/file")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_branding(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/branding")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_change_nextavailabletime_sys_id(
        self, sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/change_request_calendar/change/nextavailabletime/{sys_id}"
        )
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_change_nextavailabletime_sys_id(
        self, sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/change_request_calendar/change/nextavailabletime/{sys_id}"
        )
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_change_related_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/change_request_calendar/change/related/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_change_request_calendar_change_sys_id_start_date_end_date(
        self, sys_id: str, start_date: str, end_date: str
    ) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
            start_date: Path parameter
            end_date: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/change_request_calendar/change/{sys_id}/{start_date}/{end_date}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_change_request_calendar_glidestack_action(
        self, action: str
    ) -> ServiceNowResponse:
        """
        Args:
            action: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/change_request_calendar/glidestack/{action}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def patch_now_change_request_calendar_table_sysid(
        self, table: str, sysid: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            table: Path parameter
            sysid: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/change_request_calendar/{table}/{sysid}")
        params = {}

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_check_plugin_installation_is_plugin_installed_pluginId(
        self, pluginId: str
    ) -> ServiceNowResponse:
        """
        Args:
            pluginId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/check_plugin_installation/is_plugin_installed/{pluginId}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cilifecyclemgmt_actions(
        self,
        data: Dict[str, Any],
        requestorId: str,
        sysIds: str,
        actionName: str,
        oldActionNames: Optional[str] = None,
        leaseTime: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Add CI Action
        Args:
            data: Request body data
            requestorId: Requestor Id
            sysIds: List of sys_ids
            actionName: CI Action Name
            oldActionNames: List of old Action Names that ALL CIs should be in
            leaseTime: Lease Time in HH:MM:SS
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/actions")
        params = self._build_params(
            requestorId=requestorId,
            sysIds=sysIds,
            actionName=actionName,
            oldActionNames=oldActionNames,
            leaseTime=leaseTime,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_cilifecyclemgmt_actions(
        self, requestorId: str, sysIds: str, actionName: str
    ) -> ServiceNowResponse:
        """Remove CI Action
        Args:
            requestorId: Requestor Id
            sysIds: List of sys_ids
            actionName: CI Action Name
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/actions")
        params = self._build_params(
            requestorId=requestorId, sysIds=sysIds, actionName=actionName
        )

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cilifecyclemgmt_actions_sys_id(
        self, sys_id: str
    ) -> ServiceNowResponse:
        """Get CI Actions
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cilifecyclemgmt/actions/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cilifecyclemgmt_compatActions(
        self, actionName: str, otherActionName: str
    ) -> ServiceNowResponse:
        """Compatible Actions
        Args:
            actionName: CI Action name
            otherActionName: Other CI Action name
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/compatActions")
        params = self._build_params(
            actionName=actionName, otherActionName=otherActionName
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cilifecyclemgmt_distinctStatuses(
        self,
        data: Dict[str, Any],
        requestorId: str,
        sysIds: str,
        opsLabels: str,
        oldOpsLabels: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Set Distinct Operational States
        Args:
            data: Request body data
            requestorId: Requestor Id
            sysIds: List of sys_ids
            opsLabels: List of operational status labels to be set
            oldOpsLabels: List of old operational status labels that each CI should be in
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/distinctStatuses")
        params = self._build_params(
            requestorId=requestorId,
            sysIds=sysIds,
            opsLabels=opsLabels,
            oldOpsLabels=oldOpsLabels,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def patch_cilifecyclemgmt_leases_sys_id(
        self,
        sys_id: str,
        data: Dict[str, Any],
        requestorId: str,
        actionName: str,
        leaseTime: str,
    ) -> ServiceNowResponse:
        """Extend Lease
        Args:
            sys_id: Path parameter
            data: Request body data
            requestorId: Requestor Id
            actionName: CI Action Name
            leaseTime: Lease Time in HH:MM:SS
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cilifecyclemgmt/leases/{sys_id}")
        params = self._build_params(
            requestorId=requestorId, actionName=actionName, leaseTime=leaseTime
        )

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_leases_expired_sys_id(
        self, sys_id: str, requestorId: str, actionName: str
    ) -> ServiceNowResponse:
        """Lease Expired
        Args:
            sys_id: Path parameter
            requestorId: Requestor Id
            actionName: CI Action Name
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cilifecyclemgmt/leases/{sys_id}/expired")
        params = self._build_params(requestorId=requestorId, actionName=actionName)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cilifecyclemgmt_notAllowedAction(
        self, ciClass: str, opsLabel: str, actionName: str
    ) -> ServiceNowResponse:
        """Not Allowed Action
        Args:
            ciClass: CI class
            opsLabel: Operational status label
            actionName: CI Action name
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/notAllowedAction")
        params = self._build_params(
            ciClass=ciClass, opsLabel=opsLabel, actionName=actionName
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cilifecyclemgmt_notAllowedOpsTransition(
        self, ciClass: str, opsLabel: str, transitionOpsLabel: str
    ) -> ServiceNowResponse:
        """Not Allowed Operational Transition
        Args:
            ciClass: CI class
            opsLabel: Operational status label
            transitionOpsLabel: Operational status label to transition to
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/notAllowedOpsTransition")
        params = self._build_params(
            ciClass=ciClass, opsLabel=opsLabel, transitionOpsLabel=transitionOpsLabel
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cilifecyclemgmt_operators(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Register Operator
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/operators")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_cilifecyclemgmt_operators_req_id(
        self, req_id: str
    ) -> ServiceNowResponse:
        """Unregister Operator
        Args:
            req_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cilifecyclemgmt/operators/{req_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_requestors_valid_req_id(self, req_id: str) -> ServiceNowResponse:
        """Validate Requestor
        Args:
            req_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cilifecyclemgmt/requestors/{req_id}/valid")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cilifecyclemgmt_statuses(
        self,
        data: Dict[str, Any],
        requestorId: str,
        sysIds: str,
        opsLabel: str,
        oldOpsLabels: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Set Operational State
        Args:
            data: Request body data
            requestorId: Requestor Id
            sysIds: List of sys_ids
            opsLabel: Operational status label
            oldOpsLabels: List of old operational status labels that each CI should be in
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cilifecyclemgmt/statuses")
        params = self._build_params(
            requestorId=requestorId,
            sysIds=sysIds,
            opsLabel=opsLabel,
            oldOpsLabels=oldOpsLabels,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cilifecyclemgmt_statuses_sys_id(
        self, sys_id: str
    ) -> ServiceNowResponse:
        """Get Operational State
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cilifecyclemgmt/statuses/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_data_manager_user_groups(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/data_manager_user_groups")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_deprecation(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/deprecation")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ci_lifecycle_manager_edit_exclusion_list(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/edit_exclusion_list")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_enforced_condition(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/enforced_condition")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ci_lifecycle_manager_exclude_cis(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/exclude_cis")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_excluded_cis_list_per_policy_type(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            "/ci_lifecycle_manager/excluded_cis_list_per_policy_type"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_excluded_cis_per_policy_type(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/excluded_cis_per_policy_type")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_full_encoded_query(self) -> ServiceNowResponse:
        """Combines the policy query with the exclusion list and retirement definition
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/full_encoded_query")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_policies(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/policies")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_policy(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/policy")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ci_lifecycle_manager_policy(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/policy")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_ci_lifecycle_manager_policy(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/policy")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_ci_lifecycle_manager_policy_sys_id(
        self, sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ci_lifecycle_manager/policy/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_policy_count(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/policy_count")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_policy_types(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/policy_types")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_preview_metadata(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/preview_metadata")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_related_entry_tables(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/related_entry_tables")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_ci_lifecycle_manager_status_sys_id(
        self, sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ci_lifecycle_manager/status/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_sub_flows(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/sub_flows")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_task_group_fields(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/task_group_fields")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ci_lifecycle_manager_tasks_count(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ci_lifecycle_manager/tasks_count")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_clientextension_name(
        self, name: str, sysparm_scope: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get all UI Scripts which are registered instances of a Client Extension Point
        Args:
            name: Path parameter
            sysparm_scope: The scope of the extension point
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/clientextension/{name}")
        params = self._build_params(sysparm_scope=sysparm_scope)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_clone_auth_getInstanceDetails(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/clone_auth/getInstanceDetails")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_clone_request_calendar_clone_instanceId_start_date_end_date(
        self, instanceId: str, start_date: str, end_date: str
    ) -> ServiceNowResponse:
        """
        Args:
            instanceId: Path parameter
            start_date: Path parameter
            end_date: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/clone_request_calendar/clone/{instanceId}/{start_date}/{end_date}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cmdb_ingest_data_source_sys_id(
        self, data_source_sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Create a record to be ingested by the CMDB
        Args:
            data_source_sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/ingest/{data_source_sys_id}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cmdb_instance_className(
        self,
        className: str,
        sysparm_query: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_offset: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Query records for a CMDB class
        Args:
            className: Path parameter
            sysparm_query: An encoded query string used to filter the results
            sysparm_limit: The maximum number of results returned per page (default: 1000)
            sysparm_offset: A number of records to exclude from the query (default: 0)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/instance/{className}")
        params = self._build_params(
            sysparm_query=sysparm_query,
            sysparm_limit=sysparm_limit,
            sysparm_offset=sysparm_offset,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cmdb_instance_className(
        self, className: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Create a record with associated relations
        Args:
            className: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/instance/{className}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cmdb_instance_className_sys_id(
        self, className: str, sys_id: str
    ) -> ServiceNowResponse:
        """Query attributes and relationship information for a specific record
        Args:
            className: Path parameter
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/instance/{className}/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_cmdb_instance_className_sys_id(
        self, className: str, sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Replace CI record
        Args:
            className: Path parameter
            sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/instance/{className}/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def patch_cmdb_instance_className_sys_id(
        self, className: str, sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Update CI record
        Args:
            className: Path parameter
            sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/instance/{className}/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_instance_relation_className_sys_id(
        self, className: str, sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Create Relation for the CI
        Args:
            className: Path parameter
            sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/instance/{className}/{sys_id}/relation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_instance_relation_className_sys_id_rel_sys_id(
        self, className: str, sys_id: str, rel_sys_id: str
    ) -> ServiceNowResponse:
        """Delete Relation for the CI
        Args:
            className: Path parameter
            sys_id: Path parameter
            rel_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/cmdb/instance/{className}/{sys_id}/relation/{rel_sys_id}"
        )
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cmdb_meta_className(self, className: str) -> ServiceNowResponse:
        """Query metadata for a CMDB class
        Args:
            className: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb/meta/{className}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cmdb_ui_api_IdentificationGetParentClasses_className(
        self, className: str
    ) -> ServiceNowResponse:
        """
        Args:
            className: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/cmdb_ui_api/IdentificationGetParentClasses/{className}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cmdb_ui_api_IdentificationLookUpTables_className(
        self, className: str
    ) -> ServiceNowResponse:
        """
        Args:
            className: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb_ui_api/IdentificationLookUpTables/{className}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cmdb_ui_api_validate_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cmdb_ui_api/validate/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cmdb_workspace_api_encodedquery(
        self, table: Optional[str] = None, query: Optional[str] = None
    ) -> ServiceNowResponse:
        """Returns a friendly display name of an encoded query.
        Args:
            table: The table name (not display name) used in the operation.
            query: The encodedquery associated with the GlideRecord
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cmdb_workspace_api/encodedquery")
        params = self._build_params(table=table, query=query)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_collaboration_chat_event_processor_chats(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/collaboration_chat_event_processor/chats")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_v1_context_doc_url_resourceid(
        self, resourceid: str
    ) -> ServiceNowResponse:
        """Converts help resourceid to a help url with version. Redirects to SNC doc site.
        Args:
            resourceid: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/context_doc_url/{resourceid}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_action_handler(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs/action_handler")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_feedback(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs/feedback")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_feedback_link(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs/feedback/link")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_get_actions_by_condition(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs/get_actions_by_condition")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cxs_get_latest_worknote_table_name_record_id(
        self, table_name: str, record_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            table_name: Path parameter
            record_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cxs/get_latest_worknote/{table_name}/{record_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_cxs_search(
        self,
        start: Optional[str] = None,
        cx: Optional[str] = None,
        num: Optional[str] = None,
        q: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            start: The index of the first result to return
            cx: The search context, either sys_id or name
            num: The number of resuts to return
            q: Query String
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs/search")
        params = self._build_params(start=start, cx=cx, num=num, q=q)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_search(
        self,
        data: Dict[str, Any],
        q: Optional[str] = None,
        cx: Optional[str] = None,
        start: Optional[str] = None,
        num: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
            q: Query String
            cx: The search context, either sys_id or name
            start: The index of the first result to return
            num: The number of resuts to return
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs/search")
        params = self._build_params(q=q, cx=cx, start=start, num=num)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_attach(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/attach")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_copy_incident_resolution(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/copy_incident_resolution")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_chg_to_inc(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_chg_to_inc")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_inc_to_chg(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_inc_to_chg")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_inc_to_chg_req(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_inc_to_chg_req")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_inc_to_chg_rest(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_inc_to_chg_rest")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_inc_to_outage(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_inc_to_outage")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_inc_to_prb(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_inc_to_prb")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_inc_to_prb_rest(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_inc_to_prb_rest")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_incident(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_incident")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_incident_rest(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_incident_rest")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_prb_to_chg_req(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_prb_to_chg_req")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_prb_to_inc(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_prb_to_inc")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_prb_to_incident(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_prb_to_incident")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_cxs_actions_link_prb_to_outage(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cxs_actions/link_prb_to_outage")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_consumerAccount_conversation(
        self,
        data: Dict[str, Any],
        conversationType: Optional[str] = None,
        count: Optional[str] = None,
        userId: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Fetch conversations tied to the user based on conversation type and count
        Args:
            data: Request body data
            conversationType: Type of Conversation
            count: Number of conversations to fetch
            userId: ID of the user
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/cs/consumerAccount/conversation")
        params = self._build_params(
            conversationType=conversationType, count=count, userId=userId
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_consumerAccount_read(
        self,
        data: Dict[str, Any],
        messageId: Optional[str] = None,
        conversationId: Optional[str] = None,
        sysparm_deviceType: Optional[str] = None,
        identifier: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Update last read message for a consumer account
        Args:
            data: Request body data
            messageId: Message ID to mark as read
            conversationId: VA Conversation Id
            sysparm_deviceType: Device type
            identifier: identifier , can be portal/ app/ page id
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/cs/consumerAccount/read")
        params = self._build_params(
            messageId=messageId,
            conversationId=conversationId,
            sysparm_deviceType=sysparm_deviceType,
            identifier=identifier,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_consumerAccount_unreadConversation(
        self,
        portal: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_return_only: Optional[str] = None,
        client_type: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Get unread messages which are associated with the currently logged in user's consumer account.
        Args:
            portal: Portal Name
            sysparm_limit: Number of Messages
            sysparm_return_only: Return only the count or only the preview
            client_type: client type to be included for mobile
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/cs/consumerAccount/unreadConversation")
        params = self._build_params(
            portal=portal,
            sysparm_limit=sysparm_limit,
            sysparm_return_only=sysparm_return_only,
            client_type=client_type,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_consumerAccount_unreadConversationCount(
        self, sysparm_deviceType: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get unread conversation count which are associated with the currently logged in user's consumer account.
        Args:
            sysparm_deviceType: Device Type
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/cs/consumerAccount/unreadConversationCount")
        params = self._build_params(sysparm_deviceType=sysparm_deviceType)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_consumerAccount_unreadMessage(
        self,
        sysparm_limit: Optional[str] = None,
        sysparm_return_only: Optional[str] = None,
        portal: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Get unread messages which are associated with the currently logged in user's consumer account.
        Args:
            sysparm_limit: Number of Messages
            sysparm_return_only: Return only the count or only the preview
            portal: Portal Name
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/cs/consumerAccount/unreadMessage")
        params = self._build_params(
            sysparm_limit=sysparm_limit,
            sysparm_return_only=sysparm_return_only,
            portal=portal,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_consumerAccount_conversation_consumerAccountId(
        self,
        consumerAccountId: str,
        data: Dict[str, Any],
        closedConversationCount: Optional[str] = None,
        activeConversationCount: Optional[str] = None,
        notificationConversationCount: Optional[str] = None,
        archivedChatEnabled: Optional[str] = None,
        conversationId: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Fetch conversations tied to the consumer account based on conversation type and count
        Args:
            consumerAccountId: Path parameter
            data: Request body data
            closedConversationCount: Number of closed conversations to fetch
            activeConversationCount: Number of active conversations to fetch
            notificationConversationCount: Number of notification conversations to fetch
            archivedChatEnabled: Include archived chats in the unread count
            conversationId: conversation currently active on client
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/v1/cs/consumerAccount/{consumerAccountId}/conversation"
        )
        params = self._build_params(
            closedConversationCount=closedConversationCount,
            activeConversationCount=activeConversationCount,
            notificationConversationCount=notificationConversationCount,
            archivedChatEnabled=archivedChatEnabled,
            conversationId=conversationId,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_consumerAccount_message_consumerAccountId(
        self,
        consumerAccountId: str,
        lastMessageId: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_sort: Optional[str] = None,
        sysparm_age: Optional[str] = None,
        sysparm_deviceType: Optional[str] = None,
        conversationId: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Get all topics and messages which are associated to this consumer account.
        Args:
            consumerAccountId: Path parameter
            lastMessageId: Last Message Id
            sysparm_limit: Number of Messages
            sysparm_sort: asc/desc order of messages
            sysparm_age: older/newer messages (only applicable when lastMessageId is provided)
            sysparm_deviceType: Device Type
            conversationId: VA Conversation ID
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/cs/consumerAccount/{consumerAccountId}/message")
        params = self._build_params(
            lastMessageId=lastMessageId,
            sysparm_limit=sysparm_limit,
            sysparm_sort=sysparm_sort,
            sysparm_age=sysparm_age,
            sysparm_deviceType=sysparm_deviceType,
            conversationId=conversationId,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_latest_auxiliary_consumerAccountId(
        self, consumerAccountId: str, conversationId: Optional[str] = None
    ) -> ServiceNowResponse:
        """Fetch all of the latest auxiliary information for a given conversation
        Args:
            consumerAccountId: Path parameter
            conversationId: VA Conversation ID
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/v1/cs/consumerAccount/{consumerAccountId}/nowAssist/latest/auxiliary"
        )
        params = self._build_params(conversationId=conversationId)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_consumerAccount_sync_consumerAccountId(
        self,
        consumerAccountId: str,
        snCsSessionId: Optional[str] = None,
        serialNumber: Optional[str] = None,
        conversationId: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Fetch all messages since a serial number for a given session.
        Args:
            consumerAccountId: Path parameter
            snCsSessionId: VA Session ID
            serialNumber: Last serial number received
            conversationId: VA Conversation ID
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/cs/consumerAccount/{consumerAccountId}/sync")
        params = self._build_params(
            snCsSessionId=snCsSessionId,
            serialNumber=serialNumber,
            conversationId=conversationId,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_consumerAccount_sync_consumerAccountId(
        self,
        consumerAccountId: str,
        data: Dict[str, Any],
        snCsSessionId: Optional[str] = None,
        serialNumber: Optional[str] = None,
        conversationId: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Fetch all messages since a serial number for a given session and update the last read status
        Args:
            consumerAccountId: Path parameter
            data: Request body data
            snCsSessionId: VA Session ID
            serialNumber: Last serial number received/read
            conversationId: VA Conversation ID
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/cs/consumerAccount/{consumerAccountId}/sync")
        params = self._build_params(
            snCsSessionId=snCsSessionId,
            serialNumber=serialNumber,
            conversationId=conversationId,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_member_drop_user_id(
        self, user_id: str, data: Dict[str, Any], interaction_id: Optional[str] = None
    ) -> ServiceNowResponse:
        """Drop agent from a conversation.
        Args:
            user_id: Path parameter
            data: Request body data
            interaction_id: Interaction for which the agent needs to be dropped as a member.
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/conversation/member/{user_id}/drop")
        params = self._build_params(interaction_id=interaction_id)

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_member_update_user_id(
        self, user_id: str, data: Dict[str, Any], role: Optional[str] = None
    ) -> ServiceNowResponse:
        """Update agent's memberType in a conversation.
        Args:
            user_id: Path parameter
            data: Request body data
            role: The role to which the member type needs to be updated to.
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/conversation/member/{user_id}/update")
        params = self._build_params(role=role)

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_cs_message(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/cs_message")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_cs_preview_logging_flag(
        self, flag: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            flag: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/cs_preview/logging/{flag}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_copy_assessments_copy(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/copy_assessments/copy")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_ci_types(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/ci_types")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_csdm_app_service_create_app_service(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/create_app_service")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_csdm_app_service_delete_app_service(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/delete_app_service")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_environment_choice_list(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/environment_choice_list")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_get_app_service(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/get_app_service")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_csdm_app_service_get_cis_for_tags(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/get_cis_for_tags")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_get_tag_value_list(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/get_tag_value_list")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_operational_status_choice_list(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/operational_status_choice_list")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_relation(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/relation")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_search_cmdb_groups(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/search_cmdb_groups")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_search_groups(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/search_groups")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_search_manual_ci(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/search_manual_ci")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_search_model_id(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/search_model_id")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_search_product_models(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/search_product_models")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_search_users(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/search_users")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_tag_based_service_candidates(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/tag_based_service_candidates")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_tag_based_service_families(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/tag_based_service_families")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_tag_based_service_family_category(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/tag_based_service_family_category")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_csdm_app_service_update_app_service(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/update_app_service")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_csdm_app_service_validate_app_service(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/validate_app_service")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_csdm_app_service_validate_entry_point(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/validate_entry_point")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_csdm_app_service_validate_tag_list(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/csdm_app_service/validate_tag_list")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_v1_attachment_csm(
        self,
        sysparm_query: Optional[str] = None,
        sysparm_suppress_pagination_header: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_query_category: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Retrieve metadata for attachments
        Args:
            sysparm_query: An encoded query string used to filter the results (relative to Attachment table)
            sysparm_suppress_pagination_header: True to supress pagination header (default: false)
            sysparm_limit: The maximum number of results returned per page (default: 10,000)
            sysparm_query_category: Name of the query category (read replica category) to use for queries
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/attachment_csm")
        params = self._build_params(
            sysparm_query=sysparm_query,
            sysparm_suppress_pagination_header=sysparm_suppress_pagination_header,
            sysparm_limit=sysparm_limit,
            sysparm_query_category=sysparm_query_category,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_attachment_csm_file(
        self,
        data: Dict[str, Any],
        table_name: str,
        table_sys_id: str,
        file_name: str,
        encryption_context: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Upload an attachment from a binary request
        Args:
            data: Request body data
            table_name: Table to attach the file to
            table_sys_id: Record to attach the file to
            file_name: File name for the attachment
            encryption_context: Encryption context to be used if file to be saved encrypted
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/attachment_csm/file")
        params = self._build_params(
            table_name=table_name,
            table_sys_id=table_sys_id,
            file_name=file_name,
            encryption_context=encryption_context,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_attachment_csm_upload(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Upload an attachment from a multipart form
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/attachment_csm/upload")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_v1_attachment_csm_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """Retrieve attachment metadata
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/attachment_csm/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_v1_attachment_csm_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """Delete an attachment
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/attachment_csm/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_attachment_csm_file_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """Retrieve attachment content
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/attachment_csm/{sys_id}/file")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_data_classification_addClassification(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/data_classification/addClassification")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_data_classification_classify(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/data_classification/classify")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_data_classification_clear(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/data_classification/clear")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_data_classification_getAllDataClasses(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/data_classification/getAllDataClasses")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_data_classification_getClassification(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/data_classification/getClassification")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_documents_generation_api_redact(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/documents_generation_api/redact")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_v1_email(self, data: Dict[str, Any]) -> ServiceNowResponse:
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
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_v1_email_id(
        self, id: str, sysparm_fields: Optional[str] = None
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
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_querybuilder_exportquery(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/querybuilder/exportquery")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_follow_notifications_action(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/follow_notifications/action")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_feedback(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/feedback")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_geomap(
        self,
        field: str,
        table: str,
        mapSysId: Optional[str] = None,
        mapKey: Optional[str] = None,
        useLatLon: Optional[str] = None,
        isManual: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            mapSysId:
            mapKey:
            useLatLon: Boolean value to fetch latitude and longitude data
            isManual:
            field:
            table:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/geomap")
        params = self._build_params(
            mapSysId=mapSysId,
            mapKey=mapKey,
            useLatLon=useLatLon,
            isManual=isManual,
            field=field,
            table=table,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_get_core_ui_logo_source_getCoreLogoSrc(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/get_core_ui_logo_source/getCoreLogoSrc")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_analytics_events(
        self,
        data: Dict[str, Any],
        sysparm_event_action: str,
        sysparm_event_priority: str,
        sysparm_track_async: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Analytics API to track Events
        Args:
            data: Request body data
            sysparm_event_action: Event Action
            sysparm_event_priority: Event Priority
            sysparm_track_async: Track Async
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/analytics/events")
        params = self._build_params(
            sysparm_event_action=sysparm_event_action,
            sysparm_event_priority=sysparm_event_priority,
            sysparm_track_async=sysparm_track_async,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_analytics_updateContext(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """API to Set Context Attributes on the Glide Session Properties
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/analytics/updateContext")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_global_file_management_existingFileSearch(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/global_file_management/existingFileSearch")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_global_file_management_moveFiles(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/global_file_management/moveFiles")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_globalsearch_groups(self) -> ServiceNowResponse:
        """Get an ordered list of all of the search groups available to the current user
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/globalsearch/groups")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_globalsearch_search(
        self, sysparm_search: str, sysparm_groups: Optional[str] = None
    ) -> ServiceNowResponse:
        """Search group[s] with a keyword-based query
        Args:
            sysparm_groups: A comma-separated list of ids of the groups to search within (default searches all groups)
            sysparm_search: The keyword(s) to search for
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/globalsearch/search")
        params = self._build_params(
            sysparm_groups=sysparm_groups, sysparm_search=sysparm_search
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_graphql(self) -> ServiceNowResponse:
        """Submit a GraphQL query
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/graphql")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_graphql(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Submit a GraphQL query
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/graphql")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_log_update(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_setup/change/log/update")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_content_children_contentTable_parentId(
        self, contentTable: str, parentId: str
    ) -> ServiceNowResponse:
        """
        Args:
            contentTable: Path parameter
            parentId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/guided_setup/content/children/{contentTable}/{parentId}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_guided_setup_content_contentId(
        self, contentId: str
    ) -> ServiceNowResponse:
        """
        Args:
            contentId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_setup/content/{contentId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_content_root_contentId(self, contentId: str) -> ServiceNowResponse:
        """
        Args:
            contentId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_setup/content/{contentId}/root")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_embedded_help_actions_content_action(
        self, content: str, action: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            content: Path parameter
            action: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_setup/embedded_help/actions/{content}/{action}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_guided_setup_nav_action(
        self, action: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            action: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_setup/nav/{action}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_task_update(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_setup/task/update")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_guided_tours_analytics(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/analytics")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_autolaunch_get(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/autolaunch/get")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_autolaunch_override(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/autolaunch/override")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_autolaunch_tour(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/autolaunch/tour")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_autolaunch_tour(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/autolaunch/tour")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_guided_tours_isactive(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/isactive")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_guided_tours_page(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/page")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_platform_page_name(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/platform/page_name")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_guided_tours_tours(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/tours")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_guided_tours_tours(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/guided_tours/tours")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_guided_tours_tours_id(self, id: str) -> ServiceNowResponse:
        """
        Args:
            id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_tours/tours/{id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_guided_tours_tours_id(
        self, id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_tours/tours/{id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_tours_steps_tour_id(self, tour_id: str) -> ServiceNowResponse:
        """
        Args:
            tour_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_tours/tours/{tour_id}/steps")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_tours_steps_tour_id(
        self, tour_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            tour_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_tours/tours/{tour_id}/steps")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_tours_steps_tour_id_step_id(
        self, tour_id: str, step_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            tour_id: Path parameter
            step_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_tours/tours/{tour_id}/steps/{step_id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_tours_steps_tour_id_step_id(
        self, tour_id: str, step_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            tour_id: Path parameter
            step_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/guided_tours/tours/{tour_id}/steps/{step_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_identifyreconcile(
        self, data: Dict[str, Any], sysparm_data_source: Optional[str] = None
    ) -> ServiceNowResponse:
        """Create or Update CI
        Args:
            data: Request body data
            sysparm_data_source: Data Source calling the API
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/identifyreconcile")
        params = self._build_params(sysparm_data_source=sysparm_data_source)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_identifyreconcile_enhanced(
        self,
        data: Dict[str, Any],
        sysparm_data_source: Optional[str] = None,
        options: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Create or Update CI Enhanced
        Args:
            data: Request body data
            sysparm_data_source: Data Source calling the API
            options: A comma-separated list of key:value pairs of Enhanced IRE options. For example: partial_payload:true,partial_commits:true
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/identifyreconcile/enhanced")
        params = self._build_params(
            sysparm_data_source=sysparm_data_source, options=options
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_identifyreconcile_query(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Identify CI
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/identifyreconcile/query")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_identifyreconcile_queryEnhanced(
        self,
        data: Dict[str, Any],
        sysparm_data_source: Optional[str] = None,
        options: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Identify CI Enhanced
        Args:
            data: Request body data
            sysparm_data_source: Data Source calling the API
            options: A comma-separated list of key:value pairs of Enhanced IRE options. For example: partial_payload:true,partial_commits:true
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/identifyreconcile/queryEnhanced")
        params = self._build_params(
            sysparm_data_source=sysparm_data_source, options=options
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_build_build_application(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ide/build/build-application")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_build_convert_application(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ide/build/convert-application")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_build_scaffold_application(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ide/build/scaffold-application")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_build_sync_application(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ide/build/sync-application")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_importprogresschecker_getStatus(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/importprogresschecker/getStatus")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_import_stagingTableName(
        self, stagingTableName: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Create a record in an Import Set staging table
        Args:
            stagingTableName: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/import/{stagingTableName}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_import_insertMultiple_stagingTableName(
        self, stagingTableName: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Insert Multiple Records from same request
        Args:
            stagingTableName: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/import/{stagingTableName}/insertMultiple")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_import_stagingTableName_sys_id(
        self, stagingTableName: str, sys_id: str
    ) -> ServiceNowResponse:
        """Retrieve an Import Set record
        Args:
            stagingTableName: Path parameter
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/import/{stagingTableName}/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_initiate_message_validate_phone_number(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/initiate_message/validate_phone_number")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_initiate_message_validate_profile(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/initiate_message/validate_profile")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_interaction(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Create An Interaction
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/interaction")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_interaction_close_interaction_id(
        self, interaction_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Close an Interaction
        Args:
            interaction_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/interaction/{interaction_id}/close")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_interactive_analysis_table_tableName(
        self, tableName: str, sysparm_list_view: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            tableName: Path parameter
            sysparm_list_view: List's view name.
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/interactive_analysis/table/{tableName}")
        params = self._build_params(sysparm_list_view=sysparm_list_view)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_interactive_analysis_table_tableName(
        self,
        tableName: str,
        data: Dict[str, Any],
        sysparm_list_view: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            tableName: Path parameter
            data: Request body data
            sysparm_list_view: List's view name.
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/interactive_analysis/table/{tableName}")
        params = self._build_params(sysparm_list_view=sysparm_list_view)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_interactive_analysis_table_tableName(
        self, tableName: str, sysparm_list_view: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            tableName: Path parameter
            sysparm_list_view: List's view name.
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/interactive_analysis/table/{tableName}")
        params = self._build_params(sysparm_list_view=sysparm_list_view)

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_life_cycle_api_validate(
        self,
        table: Optional[str] = None,
        life_cycle_stage_status: Optional[str] = None,
        life_cycle_stage: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            table:
            life_cycle_stage_status:
            life_cycle_stage:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/life_cycle_api/validate")
        params = self._build_params(
            table=table,
            life_cycle_stage_status=life_cycle_stage_status,
            life_cycle_stage=life_cycle_stage,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_life_cycle_api_value(
        self, table: Optional[str] = None, life_cycle_stage: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            table:
            life_cycle_stage:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/life_cycle_api/value")
        params = self._build_params(table=table, life_cycle_stage=life_cycle_stage)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_list_query(
        self,
        sysparm_operator: Optional[str] = None,
        sysparm_field: Optional[str] = None,
        sysparm_sys_id: Optional[str] = None,
        sysparm_table: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            sysparm_operator: The query operator
            sysparm_field: The name for the field/column to be queried
            sysparm_sys_id: The sys_id of the record selected
            sysparm_table: Table name
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/list_query")
        params = self._build_params(
            sysparm_operator=sysparm_operator,
            sysparm_field=sysparm_field,
            sysparm_sys_id=sysparm_sys_id,
            sysparm_table=sysparm_table,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_manual_ci_addManualCI(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/manual_ci/addManualCI")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_manual_ci_doSkipAndResume(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/manual_ci/doSkipAndResume")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_manual_ci_getBSDomainId(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/manual_ci/getBSDomainId")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_manual_ci_getCITypes(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/manual_ci/getCITypes")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_manual_ci_getCIsByType(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/manual_ci/getCIsByType")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_manual_ci_removeManualEP(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/manual_ci/removeManualEP")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sn_map_data_id(
        self, id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sn_map/data/{id}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_mid_telemetry_metrics(
        self, data: Dict[str, Any], mid_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
            mid_sys_id: Sys ID to identify MID Server
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mid_telemetry/metrics")
        params = self._build_params(mid_sys_id=mid_sys_id)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ml_instance_validation_validateToken_token(
        self, token: str
    ) -> ServiceNowResponse:
        """
        Args:
            token: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ml_instance_validation/validateToken/{token}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_aggregate_daily_items_counts(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Retrieve items count per day within a given timespan
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/aggregate/daily_items_counts")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_alert(
        self, data: Dict[str, Any], ScreenId: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get an alert from a form screen
        Args:
            data: Request body data
            ScreenId: FormScreenId
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/alert")
        params = self._build_params(ScreenId=ScreenId)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_applet_launcher(
        self, applet_launcher_id: str
    ) -> ServiceNowResponse:
        """Get applet launcher
        Args:
            applet_launcher_id: Id of applet launcher
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/applet_launcher")
        params = self._build_params(applet_launcher_id=applet_launcher_id)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_applet_launcher_feed(self) -> ServiceNowResponse:
        """Get several types of the live feeds on the applet launcher page
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/applet_launcher/feed")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_applet_launcher_initialize(self) -> ServiceNowResponse:
        """Initialize the live feeds on the applet launcher page
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/applet_launcher/initialize")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_applet_launcher_refresh(self) -> ServiceNowResponse:
        """Refresh the live feeds on the applet launcher page
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/applet_launcher/refresh")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_badge_count(
        self, sysparm_badge_count_ids: str
    ) -> ServiceNowResponse:
        """Get multiple badge counts
        Args:
            sysparm_badge_count_ids: Badge count ids (comma separated)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/badge_count")
        params = self._build_params(sysparm_badge_count_ids=sysparm_badge_count_ids)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_badge_count_badge_count_id(
        self, badge_count_id: str
    ) -> ServiceNowResponse:
        """Get a single badge count
        Args:
            badge_count_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/badge_count/{badge_count_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_application_button(self) -> ServiceNowResponse:
        """Fetches all buttons within a given scope (if provided)
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/application_button")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_item_view_open_modal(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/item_view/open_modal")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_item_view_preview(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/item_view/preview")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_mobile_ux_themes(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/mobile_ux_themes")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_open_modal_tableName(self, tableName: str) -> ServiceNowResponse:
        """
        Args:
            tableName: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/mcb/open_modal/{tableName}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_system_property(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/system_property")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_table(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/table")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_user(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/user")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_userPreferences(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/userPreferences")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_mcb_view_config(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/view_config")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_view_config_field_viewConfigSysId(
        self, viewConfigSysId: str
    ) -> ServiceNowResponse:
        """
        Args:
            viewConfigSysId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/mcb/view_config/field/{viewConfigSysId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_view_config_open_modal(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/view_config/open_modal")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_view_config_preview(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/view_config/preview")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_view_config_sysId(self, sysId: str) -> ServiceNowResponse:
        """
        Args:
            sysId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/mcb/view_config/{sysId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def patch_mcb_view_config_sysId(
        self, sysId: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            sysId: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/mcb/view_config/{sysId}")
        params = {}

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_mcb_view_template(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/view_template")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_view_template_open_modal(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mcb/view_template/open_modal")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_view_template_sysId(self, sysId: str) -> ServiceNowResponse:
        """
        Args:
            sysId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/mcb/view_template/{sysId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def patch_mcb_view_template_sysId(
        self, sysId: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            sysId: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/mcb/view_template/{sysId}")
        params = {}

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mcb_web_color_variable_themeSysId(
        self, themeSysId: str
    ) -> ServiceNowResponse:
        """
        Args:
            themeSysId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/mcb/web_color_variable/{themeSysId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_chat_handshake_action_id(
        self, action_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Launch Virtual Agent with given information in request
        Args:
            action_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/chat_handshake/{action_id}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_filter(
        self, data: Dict[str, Any], document_id: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get Filter
        Args:
            data: Request body data
            document_id: Id of the document
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/filter")
        params = self._build_params(document_id=document_id)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_filter_list(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Get Filter List
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/filter_list")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_filter_list_display_values(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Get Filter Reference Display Values
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/filter_list_display_values")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_custom_map(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Returns a document with a custom map template containing the reference list data.
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/custom_map")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_visualization(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Get Visualization List
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/visualization")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_transactions_info(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Provides information about transactions
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/transactions_info")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_deeplink_launch_button_Payload(
        self, Payload: str
    ) -> ServiceNowResponse:
        """Get Button
        Args:
            Payload: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/deeplink/launch_button/{Payload}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_deeplink_redirect_Payload(self, Payload: str) -> ServiceNowResponse:
        """Get Redirection
        Args:
            Payload: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/deeplink/redirect/{Payload}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_document_pre_fetch(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Pre-fetch documents
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/document/pre_fetch")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_event_result(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Retrieve event result
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/event_result")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_favorite(self) -> ServiceNowResponse:
        """Get Favorites List
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/favorite")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_favorite(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Create Favorite item
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/favorite")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_favorite_navigate_favorite_id(
        self, favorite_id: str, time_zone_id: Optional[str] = None
    ) -> ServiceNowResponse:
        """Navigate to favorite screen
        Args:
            favorite_id: Path parameter
            time_zone_id: Time zone identifier
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/favorite/navigate/{favorite_id}")
        params = self._build_params(time_zone_id=time_zone_id)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_sg_favorite_favorite_id(
        self, favorite_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Update Favorite item
        Args:
            favorite_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/favorite/{favorite_id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_sg_favorite_favorite_id(
        self, favorite_id: str
    ) -> ServiceNowResponse:
        """Delete Favorite item
        Args:
            favorite_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/favorite/{favorite_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_generative_wwna_action_execute(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/generative/wwna_action_execute")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_generative_wwna_action_poll(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/generative/wwna_action_poll")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_generative_wwna_skill_config(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/generative/wwna_skill_config")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_font_glyphs(self, family_id: str) -> ServiceNowResponse:
        """Get font glyphs
        Args:
            family_id: Id of the font family
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/font/glyphs")
        params = self._build_params(family_id=family_id)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_icon_families(
        self, location: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get icon families
        Args:
            location: Optional location where icon family is allowed
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/icon/families")
        params = self._build_params(location=location)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_icon_images(self, family_id: str) -> ServiceNowResponse:
        """Get icon images
        Args:
            family_id: Id of the icon images family
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/icon/images")
        params = self._build_params(family_id=family_id)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_impersonation_session(self) -> ServiceNowResponse:
        """Get impersonation details for current session.
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/impersonation/session")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_impersonation_users(
        self, start: Optional[str] = None, filter: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get Impersonation User List
        Args:
            start: Start index
            filter: Search filter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/impersonation/users")
        params = self._build_params(start=start, filter=filter)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_offline_generate(self) -> ServiceNowResponse:
        """Request mobile offline data to be immediately generated
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/offline/generate")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_offline_incremental_result_token(
        self, result_token: str, lastUpdateTS: Optional[str] = None
    ) -> ServiceNowResponse:
        """Retrieve incremental offline data updates
        Args:
            result_token: Path parameter
            lastUpdateTS: Timestamp of the latest update that the mobile client has already received
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/offline/incremental/{result_token}")
        params = self._build_params(lastUpdateTS=lastUpdateTS)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_offline_retrieve_result_token(
        self, result_token: str
    ) -> ServiceNowResponse:
        """Retrieve previously requested mobile offline data
        Args:
            result_token: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/offline/retrieve/{result_token}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_offline_schedule_generate(self) -> ServiceNowResponse:
        """Request mobile offline data to be scheduled to be generated
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/offline/schedule_generate")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_offline_synchronize(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Synchronize
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/offline/synchronize")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_group(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Get group by pagination context
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/group")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_push_action(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Post a writeback request from a Push Action
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/push/action")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_record_document(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Post a record document
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/record/document")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_reference_input_display_values(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Returns a display values for reference inputs.
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/reference_input/display_values")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_reference_list(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Returns a document with a list template containing the reference list data.
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/reference_list")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_applications(self) -> ServiceNowResponse:
        """Get an array of applications
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/applications")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_document(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Post a document
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/document")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_item(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Get Item
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/item")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_list(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Get List
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/list")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_migrate_madrid(
        self, data: Dict[str, Any], sysparm_scope: str, sysparm_client_type: str
    ) -> ServiceNowResponse:
        """New schema migration
        Args:
            data: Request body data
            sysparm_scope: The scope sys_id
            sysparm_client_type: The client type
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/migrate/madrid")
        params = self._build_params(
            sysparm_scope=sysparm_scope, sysparm_client_type=sysparm_client_type
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_pre_auth(self) -> ServiceNowResponse:
        """Pre-Authentication API
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/pre_auth")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_preferences(
        self, sysparm_pref_name: Optional[str] = None
    ) -> ServiceNowResponse:
        """Mobile Get User Preferences
        Args:
            sysparm_pref_name: Specify preference key to get (can handle multiple keys in array)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/preferences")
        params = self._build_params(sysparm_pref_name=sysparm_pref_name)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_preferences(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Updating user preferences
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/preferences")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_schedule_user_id(self, user_id: str) -> ServiceNowResponse:
        """Get an agent's schedule given user ID
        Args:
            user_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/schedule/{user_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_validate_credentials(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Authentication API
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/validate_credentials")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_writeback(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Submit Action
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/writeback")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sg_mobile_script_include(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Execute script include
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/mobile_script_include")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_search(
        self,
        sysparm_search_config: str,
        sysparm_term: str,
        sysparm_search_app_config: Optional[str] = None,
        sysparm_nass_enabled: Optional[str] = None,
        sysparm_channel: Optional[str] = None,
        sysparm_evam_definition_id: Optional[str] = None,
        sysparm_deployment_channel_id: Optional[str] = None,
        sysparm_search_purview: Optional[str] = None,
        sysparm_sources: Optional[str] = None,
        sysparm_next_token: Optional[str] = None,
        sysparm_disable_spell_check: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Search for mobile
        Args:
            sysparm_search_config: The search config id
            sysparm_term: The keyword(s) to search for
            sysparm_search_app_config: The search application configuration id
            sysparm_nass_enabled: NASS enabled
            sysparm_channel: The AMB channel that synthesized results are published over
            sysparm_evam_definition_id: EVAM definition ID
            sysparm_deployment_channel_id: Deployment channel ID
            sysparm_search_purview: Search Purview
            sysparm_sources: Search source ids (comma separated)
            sysparm_next_token: Search next token
            sysparm_disable_spell_check: Disable spell check
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/search")
        params = self._build_params(
            sysparm_search_config=sysparm_search_config,
            sysparm_term=sysparm_term,
            sysparm_search_app_config=sysparm_search_app_config,
            sysparm_nass_enabled=sysparm_nass_enabled,
            sysparm_channel=sysparm_channel,
            sysparm_evam_definition_id=sysparm_evam_definition_id,
            sysparm_deployment_channel_id=sysparm_deployment_channel_id,
            sysparm_search_purview=sysparm_search_purview,
            sysparm_sources=sysparm_sources,
            sysparm_next_token=sysparm_next_token,
            sysparm_disable_spell_check=sysparm_disable_spell_check,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_publish_autocomplete(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Publish a search analytics event of type 'autocomplete'
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/search/analytics/publish/autocomplete")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_publish_refinement(
        self,
        data: Dict[str, Any],
        sysparm_search_context_application_id: str,
        sysparm_term: str,
        sysparm_search_config: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Publish a search analytics event of type 'Refinement'
        Args:
            data: Request body data
            sysparm_search_config: The search config id
            sysparm_search_context_application_id: The search context config application id
            sysparm_term: The keyword(s) to search for
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/search/analytics/publish/refinement")
        params = self._build_params(
            sysparm_search_config=sysparm_search_config,
            sysparm_search_context_application_id=sysparm_search_context_application_id,
            sysparm_term=sysparm_term,
        )

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_publish_resultclicked(
        self,
        data: Dict[str, Any],
        sysparm_search_context_application_id: str,
        sysparm_term: str,
        sysparm_click_rank: str,
        sysparm_search_config: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Publish a search analytics event of type 'ResultClicked'
        Args:
            data: Request body data
            sysparm_search_config: The search config id
            sysparm_search_context_application_id: The search context config application id
            sysparm_term: The keyword(s) to search for
            sysparm_click_rank: The user click rank value
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/search/analytics/publish/resultclicked")
        params = self._build_params(
            sysparm_search_config=sysparm_search_config,
            sysparm_search_context_application_id=sysparm_search_context_application_id,
            sysparm_term=sysparm_term,
            sysparm_click_rank=sysparm_click_rank,
        )

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_genius_poll_id(self, poll_id: str) -> ServiceNowResponse:
        """Query for Genius Results for Mobile Search
        Args:
            poll_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sg/search/genius/{poll_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_autocomplete(
        self,
        sysparm_search_config: str,
        sysparm_term: Optional[str] = None,
        sysparm_search_app_config: Optional[str] = None,
        sysparm_nass_enabled: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Search suggestions for mobile
        Args:
            sysparm_search_config: The search config id
            sysparm_term: The keyword to fetch search suggestions for
            sysparm_search_app_config: The search application configuration id
            sysparm_nass_enabled: NASS enabled
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/search/autocomplete")
        params = self._build_params(
            sysparm_search_config=sysparm_search_config,
            sysparm_term=sysparm_term,
            sysparm_search_app_config=sysparm_search_app_config,
            sysparm_nass_enabled=sysparm_nass_enabled,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_search_autocomplete(
        self,
        sysparm_search_config: str,
        sysparm_term: str,
        sysparm_search_app_config: Optional[str] = None,
        sysparm_nass_enabled: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Delete recent search term for mobile
        Args:
            sysparm_search_config: The search config id
            sysparm_term: The keyword to fetch search suggestions for
            sysparm_search_app_config: The search application configuration id
            sysparm_nass_enabled: NASS enabled
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/search/autocomplete")
        params = self._build_params(
            sysparm_search_config=sysparm_search_config,
            sysparm_term=sysparm_term,
            sysparm_search_app_config=sysparm_search_app_config,
            sysparm_nass_enabled=sysparm_nass_enabled,
        )

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_section_initialize(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Initialize items feeds for sections screen
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/section/initialize")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mobile_studio_get_data_items(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mobile_studio/get_data_items")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_mobile_studio_get_field_dotwalk(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/mobile_studio/get_field_dotwalk")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_theme_variant(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Update Theme Variant
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/theme/variant")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_user_client(self) -> ServiceNowResponse:
        """Get a User Client page
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/user_client")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_model_explainability_getimportantfeatures(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/model_explainability/getimportantfeatures")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_next_best_action_predict_nba_recommendation(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/next_best_action/predict_nba_recommendation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_next_best_action_train_nba_recommendation(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/next_best_action/train_nba_recommendation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlq_nlq_query_log(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlq/nlq_query_log")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlq_nlq_query_log(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlq/nlq_query_log")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_cancelLookupTraining_lookupId(
        self, lookupId: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            lookupId: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/nlu/cancelLookupTraining/{lookupId}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlu_cancelTraining_modelId(self, modelId: str) -> ServiceNowResponse:
        """
        Args:
            modelId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/nlu/cancelTraining/{modelId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_clonemodel(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/clonemodel")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_enableSysEntity(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/enableSysEntity")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlu_flowStatus_contextId(self, contextId: str) -> ServiceNowResponse:
        """
        Args:
            contextId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/nlu/flowStatus/{contextId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlu_getAllMappedIntentsCount(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/getAllMappedIntentsCount")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlu_getAllPublishedModels(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/getAllPublishedModels")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlu_getEntities(
        self, modelId: Optional[str] = None, intentId: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            modelId:
            intentId:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/getEntities")
        params = self._build_params(modelId=modelId, intentId=intentId)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_getLookupModelDetails(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/getLookupModelDetails")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_getMappedIntents(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/getMappedIntents")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlu_getModelDetails_modelId(self, modelId: str) -> ServiceNowResponse:
        """
        Args:
            modelId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/nlu/getModelDetails/{modelId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_importModelCSV(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/importModelCSV")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_importentity(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/importentity")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_importintent(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/importintent")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_nlu_modelstatus_modelId(self, modelId: str) -> ServiceNowResponse:
        """
        Args:
            modelId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/nlu/modelstatus/{modelId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_predictSync(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/predictSync")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_publish(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/publish")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_test(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/test")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_train(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/train")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_trainLookup_lookupId(
        self, lookupId: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            lookupId: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/nlu/trainLookup/{lookupId}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_updateUtterance(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/updateUtterance")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_nlu_validate(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/nlu/validate")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_addextracoverage(
        self,
        data: Dict[str, Any],
        rota_id: Optional[str] = None,
        end_date: Optional[str] = None,
        start_date: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
            rota_id: Single cmn_rota sys_id
            end_date: The end date to filter rotas by
            start_date: The start date to filter rotas by
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/addextracoverage")
        params = self._build_params(
            rota_id=rota_id, end_date=end_date, start_date=start_date
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_addoverride(
        self,
        data: Dict[str, Any],
        roster_id: str,
        start_date_time: str,
        end_date_time: str,
        user_id: str,
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
            roster_id: A Roster (cmn_rota_roster) sys_id
            start_date_time: The start date time value
            end_date_time: The end date time value
            user_id: A User (sys_user) sys_id
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/addoverride")
        params = self._build_params(
            roster_id=roster_id,
            start_date_time=start_date_time,
            end_date_time=end_date_time,
            user_id=user_id,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_addtimeoff(
        self,
        data: Dict[str, Any],
        end_date_time: str,
        start_date_time: str,
        user_id: str,
        group_id: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
            end_date_time: The end date time value
            start_date_time: The start date time value
            group_id: A single Group (sys_user_group) sys_id
            user_id: A User (sys_user) sys_id
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/addtimeoff")
        params = self._build_params(
            end_date_time=end_date_time,
            start_date_time=start_date_time,
            group_id=group_id,
            user_id=user_id,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_contact_preferences_comm_types(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/contact_preferences/comm_types")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_contact_preferences_save(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/contact_preferences/save")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_on_call_rota_contact_preferences_contact_pref_sys_id(
        self, contact_pref_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            contact_pref_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/on_call_rota/contact_preferences/{contact_pref_sys_id}"
        )
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_contact_preferences_type_sysId(
        self, type: str, sysId: str
    ) -> ServiceNowResponse:
        """
        Args:
            type: Path parameter
            sysId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/contact_preferences/{type}/{sysId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_creation_wizard_deleteRoster(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/creation_wizard/deleteRoster")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_creation_wizard_deleteShift(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/creation_wizard/deleteShift")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_creation_wizard_escalationpanel(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/creation_wizard/escalationpanel")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_creation_wizard_getPreviewDetails(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/creation_wizard/getPreviewDetails")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_creation_wizard_getRostersAndMemberDetails(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            "/on_call_rota/creation_wizard/getRostersAndMemberDetails"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_creation_wizard_getShifts_group_sys_id(
        self, group_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            group_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/creation_wizard/getShifts/{group_sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_creation_wizard_shiftStateUpdate(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/creation_wizard/shiftStateUpdate")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_creationwizard_save(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/creationwizard/save")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_on_call_rota_deleteCoverage(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/deleteCoverage")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_on_call_rota_deleteUserContactPreference_contact_preference_sys_id(
        self, contact_preference_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            contact_preference_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/on_call_rota/deleteUserContactPreference/{contact_preference_sys_id}"
        )
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_on_call_rota_deleteUserPreference_user_preference_sys_id(
        self, user_preference_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            user_preference_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/on_call_rota/deleteUserPreference/{user_preference_sys_id}"
        )
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_escalation_path(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/escalation_path")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_escalations(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/escalations")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_escalations_default_rota_sys_id(
        self, rota_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            rota_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/escalations/default/{rota_sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_escalations_sets_escalation_set_sys_id(
        self, escalation_set_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            escalation_set_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/escalations/sets/{escalation_set_sys_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_escalations_steps_escalation_step_sys_id(
        self, escalation_step_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            escalation_step_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/on_call_rota/escalations/steps/{escalation_step_sys_id}"
        )
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_escalations_escalation_set_sys_id(
        self, escalation_set_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            escalation_set_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/escalations/{escalation_set_sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_steps_incrementescalationlevel_escalation_set_sys_id(
        self, escalation_set_sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            escalation_set_sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/on_call_rota/escalations/{escalation_set_sys_id}/steps/incrementescalationlevel"
        )
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_escalationsets(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/escalationsets")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getEscalationById(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getEscalationById")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getEscalations(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getEscalations")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getNextRotas(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getNextRotas")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getOnCallShiftTemplates(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getOnCallShiftTemplates")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getUserContactOverrides_groupSysId(
        self, groupSysId: str
    ) -> ServiceNowResponse:
        """
        Args:
            groupSysId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/getUserContactOverrides/{groupSysId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getUserContactPerAttemptPreferences_group_id(
        self, group_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            group_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/on_call_rota/getUserContactPerAttemptPreferences/{group_id}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getUserDevicesAndContacts(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getUserDevicesAndContacts")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getUserPreference(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getUserPreference")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_getrostersbyrotas(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getrostersbyrotas")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getrostersbyrotas_rota_id(
        self, rota_id: str, rota_ids: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            rota_id: Path parameter
            rota_ids: Array of Rota Sys Ids
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/getrostersbyrotas/{rota_id}")
        params = self._build_params(rota_ids=rota_ids)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_getrotasbygroup(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/getrotasbygroup")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_getrotasbygroup_group_ids(
        self, group_ids: str
    ) -> ServiceNowResponse:
        """
        Args:
            group_ids: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/getrotasbygroup/{group_ids}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_glidestack_action(
        self, action: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            action: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/glidestack/{action}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_group(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/group")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_group_members_group_id(self, group_id: str) -> ServiceNowResponse:
        """
        Args:
            group_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/group/members/{group_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_members(
        self,
        end_date_time: str,
        start_date_time: str,
        rota_ids: Optional[str] = None,
        roster_ids: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            rota_ids: Array of Rota Sys Ids
            end_date_time: The end date time value
            roster_ids: Array of Roster Sys Ids
            start_date_time: The start date time value
            group_id: A single Group (sys_user_group) sys_id
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/group/on_call/members")
        params = self._build_params(
            rota_ids=rota_ids,
            end_date_time=end_date_time,
            roster_ids=roster_ids,
            start_date_time=start_date_time,
            group_id=group_id,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_oncallgroups(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/oncallgroups")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_overrideEscalation(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/overrideEscalation")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_pending_actions(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/pending_actions")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_replaceCoverage(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/replaceCoverage")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_resetEscalation(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/resetEscalation")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_rotaUserICalendar_group_id_rota_id_user_id(
        self, group_id: str, rota_id: str, user_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            group_id: Path parameter
            rota_id: Path parameter
            user_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/on_call_rota/rotaUserICalendar/{group_id}/{rota_id}/{user_id}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_rotas(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/rotas")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_saveUserContactPreference(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/saveUserContactPreference")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_searchoncallgroups(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/searchoncallgroups")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_sections(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/sections")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_on_call_rota_sections(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/sections")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_spans(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/spans")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_subscribeCalendar_tiny_id(
        self,
        tiny_id: str,
        nolog_token: Optional[str] = None,  # Changed from ni.nolog.token
    ) -> ServiceNowResponse:
        """
        Args:
            tiny_id: Path parameter
            ni.nolog.token:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/subscribeCalendar/{tiny_id}")
        params = self._build_params(token=nolog_token)  # Updated usage

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_on_call_rota_whoisoncall(
        self,
        rota_ids: Optional[str] = None,
        roster_ids: Optional[str] = None,
        group_ids: Optional[str] = None,
        date_time: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            rota_ids: Array of Rota Sys Ids
            roster_ids: Array of Roster Sys Ids
            group_ids: The group sys_ids to filter rotas by
            date_time: The date time value
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/on_call_rota/whoisoncall")
        params = self._build_params(
            rota_ids=rota_ids,
            roster_ids=roster_ids,
            group_ids=group_ids,
            date_time=date_time,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_workbench_group_groupSysId(
        self, groupSysId: str
    ) -> ServiceNowResponse:
        """
        Args:
            groupSysId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/on_call_rota/workbench/group/{groupSysId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_scripted_capabilities_builderName(
        self, builderName: str
    ) -> ServiceNowResponse:
        """
        Args:
            builderName: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/oneextend/scripted/capabilities/{builderName}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_capability_attributes_capabilityId(
        self, capabilityId: str
    ) -> ServiceNowResponse:
        """
        Args:
            capabilityId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/oneextend/scripted/capability/attributes/{capabilityId}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_scripted_setup_and_execute(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/oneextend/scripted/setup_and_execute")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_manual_page_inspector_ui_page_names(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/page_inspector/manual_page_inspector/ui_page_names")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_par_coreui_migration(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/par_coreui_migration")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_par_coreui_migration_summary(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/par_coreui_migration/summary")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_par_coreui_migration_utils_activate_next_experience(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/par_coreui_migration_utils/activate_next_experience")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_par_migrate_classic_dashboard_layout(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/par_migrate_classic_dashboard_layout")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_par_scheduled_export_delete_export_id(
        self, export_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            export_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/par_scheduled_export/delete/{export_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_par_scheduled_export_execute_export_id(
        self, export_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            export_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/par_scheduled_export/execute/{export_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_par_scheduled_export_fetch_export_id(
        self, export_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            export_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/par_scheduled_export/fetch/{export_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_par_scheduled_export_save(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Inserts new scheduled job for export
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/par_scheduled_export/save")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def patch_par_scheduled_export_update_export_id(
        self, export_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            export_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/par_scheduled_export/update/{export_id}")
        params = {}

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_passwordexclusionlist_progress_worker_state_workerId(
        self, data: Dict[str, Any], workerId: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            workerId: Path parameter
            data: Request body data
            workerId: A progress worker ID that can be used to check the progress state
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/passwordexclusionlist/progress_worker_state/{workerId}"
        )
        params = self._build_params(workerId=workerId)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_passwordexclusionlist_reinstall(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/passwordexclusionlist/reinstall")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_passwordexclusionlist_set_group_group_id(
        self, data: Dict[str, Any], group_id: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            group_id: Path parameter
            data: Request body data
            group_id: ID for an exclusion list group to act on
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/passwordexclusionlist/set_group/{group_id}")
        params = self._build_params(group_id=group_id)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_passwordexclusionlist_uninstall(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/passwordexclusionlist/uninstall")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sn_perf_page_application(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sn_perf_page/application")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sn_perf_page_home(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sn_perf_page/home")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sn_perf_page_page(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sn_perf_page/page")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sn_perf_page_trace(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sn_perf_page/trace")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_solution_prediction(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/agent_intelligence/solution/prediction")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_solution_prediction_solution_name(
        self, solution_name: str
    ) -> ServiceNowResponse:
        """
        Args:
            solution_name: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/agent_intelligence/solution/{solution_name}/prediction"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_solution_solution_cancel_solution_name(
        self, solution_name: str
    ) -> ServiceNowResponse:
        """
        Args:
            solution_name: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/agent_intelligence/solution/{solution_name}/solution_cancel"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_solution_solution_getsolutionrecords_solution_name(
        self, solution_name: str
    ) -> ServiceNowResponse:
        """
        Args:
            solution_name: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/agent_intelligence/solution/{solution_name}/solution_getsolutionrecords"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_agent_intelligence_solution_create_training_request(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/agent_intelligence/solution_create_training_request")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_agent_intelligence_train_default_solutions(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/agent_intelligence/train_default_solutions")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_agent_intelligence_trainer_getoobsolutiondefinitions_trainer(
        self, trainer: str
    ) -> ServiceNowResponse:
        """
        Args:
            trainer: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/agent_intelligence/{trainer}/trainer_getoobsolutiondefinitions"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_push_installation_pushApplicationName(
        self, pushApplicationName: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Adds or updates tokens to receive push notifications
        Args:
            pushApplicationName: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/push/{pushApplicationName}/installation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_push_removeInstallation_pushApplicationName(
        self, pushApplicationName: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Deactivates tokens to receive push notifications
        Args:
            pushApplicationName: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/push/{pushApplicationName}/removeInstallation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_count_state_platform_pushApplicationName(
        self, platform: str, pushApplicationName: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Enable/disable sending badge count for push notifications to user
        Args:
            platform: Path parameter
            pushApplicationName: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/push/{platform}/{pushApplicationName}/badge/count/state"
        )
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_notification_remove_platform_pushApplicationName(
        self,
        platform: str,
        pushApplicationName: str,
        types: str,
        mobileRequestIds: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Delete push notifications
        Args:
            platform: Path parameter
            pushApplicationName: Path parameter
            types: Comma separated string of types
            mobileRequestIds: Comma separated string of requestIds
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/push/{platform}/{pushApplicationName}/notification/remove"
        )
        params = self._build_params(types=types, mobileRequestIds=mobileRequestIds)

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_notification_status_platform_pushApplicationName_state(
        self, platform: str, pushApplicationName: str, state: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """Read/Unread push notifications
        Args:
            platform: Path parameter
            pushApplicationName: Path parameter
            state: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/push/{platform}/{pushApplicationName}/notification/status/{state}"
        )
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_push_notifications_platform_pushApplicationName(
        self,
        platform: str,
        pushApplicationName: str,
        types: str,
        sysparm_limit: Optional[str] = None,
        sysparm_offset: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Fetch Push notifications for mobile
        Args:
            platform: Path parameter
            pushApplicationName: Path parameter
            types: Comma separated string of types
            sysparm_limit: Limit number of notifications received
            sysparm_offset: Number of rows to skip when retrieving notifications
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/push/{platform}/{pushApplicationName}/notifications")
        params = self._build_params(
            types=types, sysparm_limit=sysparm_limit, sysparm_offset=sysparm_offset
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_related_list_edit_create(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/related_list_edit/create")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_related_list_item_filter_getFilterQuery(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/related_list_item_filter/getFilterQuery")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_reporting_alias_report_id(
        self, report_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            report_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/reporting_alias/{report_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_reporting_alias_report_id(
        self, report_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            report_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/reporting_alias/{report_id}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_reporting(
        self,
        sysparm_contains: Optional[str] = None,
        sysparm_sortby: Optional[str] = None,
        sysparm_sortdir: Optional[str] = None,
        sysparm_page: Optional[str] = None,
        sysparm_per_page: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Retrieve the list of reports
        Args:
            sysparm_contains: List of comma-separated terms to search for
            sysparm_sortby: Name of the column to order by
            sysparm_sortdir: Direction of ordering: asc/desc
            sysparm_page: Starting paging (1-based)
            sysparm_per_page: Items per page
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/reporting")
        params = self._build_params(
            sysparm_contains=sysparm_contains,
            sysparm_sortby=sysparm_sortby,
            sysparm_sortdir=sysparm_sortdir,
            sysparm_page=sysparm_page,
            sysparm_per_page=sysparm_per_page,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_reporting_favorites(
        self,
        sysparm_contains: Optional[str] = None,
        sysparm_sortby: Optional[str] = None,
        sysparm_sortdir: Optional[str] = None,
        sysparm_page: Optional[str] = None,
        sysparm_per_page: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Retrieve the list of favorite reports
        Args:
            sysparm_contains: List of comma-separated terms to search for
            sysparm_sortby: Name of the column to order by
            sysparm_sortdir: Direction of ordering: asc/desc
            sysparm_page: Starting page (1-based)
            sysparm_per_page: Items per page
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/reporting/favorites")
        params = self._build_params(
            sysparm_contains=sysparm_contains,
            sysparm_sortby=sysparm_sortby,
            sysparm_sortdir=sysparm_sortdir,
            sysparm_page=sysparm_page,
            sysparm_per_page=sysparm_per_page,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_reporting_favorites(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/reporting/favorites")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_reporting_favorites(self, sysparm_uuid: str) -> ServiceNowResponse:
        """Remove a report from favorites
        Args:
            sysparm_uuid: Sys ID of the report to remove from favorites
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/reporting/favorites")
        params = self._build_params(sysparm_uuid=sysparm_uuid)

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_reporting_table_description_field_description_table_name(
        self, table_name: str
    ) -> ServiceNowResponse:
        """
        Args:
            table_name: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/reporting_table_description/field_description/{table_name}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_reporting_table_description_table_name(
        self, table_name: str
    ) -> ServiceNowResponse:
        """
        Args:
            table_name: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/reporting_table_description/{table_name}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_sa_business_service_deleteBusinessService_service_id(
        self, service_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            service_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/sa_business_service/deleteBusinessService/{service_id}"
        )
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sa_business_service_getEntryPointConnectivity(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sa_business_service/getEntryPointConnectivity")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sa_business_service_getNumberOfCandidates(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sa_business_service/getNumberOfCandidates")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sa_business_service_initialPageData(self) -> ServiceNowResponse:
        """Loads the initial data needed for loading the single business service map page
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sa_business_service/initialPageData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_sa_business_service_submitQuestionnaire(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sa_business_service/submitQuestionnaire")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sa_business_service_updateBusinessService(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sa_business_service/updateBusinessService")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_schedule_page_event_schedule_page_sys_id(
        self, schedule_page_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            schedule_page_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/schedule_page/event/{schedule_page_sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_schedule_page_event_schedule_page_sys_id(
        self, schedule_page_sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            schedule_page_sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/schedule_page/event/{schedule_page_sys_id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_schedule_page_info_info_type_schedule_page_sys_id(
        self, info_type: str, schedule_page_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            info_type: Path parameter
            schedule_page_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/schedule_page/info/{info_type}/{schedule_page_sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_schedule_page_info_info_type_schedule_page_sys_id(
        self, info_type: str, schedule_page_sys_id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            info_type: Path parameter
            schedule_page_sys_id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/schedule_page/info/{info_type}/{schedule_page_sys_id}")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_schedule_page_schedule_page_sys_id(
        self, schedule_page_sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            schedule_page_sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/schedule_page/{schedule_page_sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_pa_scorecards(
        self,
        sysparm_uuid: Optional[str] = None,
        sysparm_breakdown: Optional[str] = None,
        sysparm_breakdown_relation: Optional[str] = None,
        sysparm_elements_filter: Optional[str] = None,
        sysparm_display: Optional[str] = None,
        sysparm_favorites: Optional[str] = None,
        sysparm_key: Optional[str] = None,
        sysparm_target: Optional[str] = None,
        sysparm_contains: Optional[str] = None,
        sysparm_tags: Optional[str] = None,
        sysparm_per_page: Optional[str] = None,
        sysparm_page: Optional[str] = None,
        sysparm_sortby: Optional[str] = None,
        sysparm_sortdir: Optional[str] = None,
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_include_scores: Optional[str] = None,
        sysparm_from: Optional[str] = None,
        sysparm_to: Optional[str] = None,
        sysparm_step: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_include_available_breakdowns: Optional[str] = None,
        sysparm_include_available_aggregates: Optional[str] = None,
        sysparm_include_realtime: Optional[str] = None,
        sysparm_include_target_color_scheme: Optional[str] = None,
        sysparm_include_forecast_scores: Optional[str] = None,
        sysparm_include_trendline_scores: Optional[str] = None,
        sysparm_include_prediction_interval: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Retrieve list of Scorecards
        Args:
            sysparm_uuid: the scope of the scorecard or list of breakdown scorecards, or a comma-separated list of scopes
            sysparm_breakdown: sys_id of the breakdown for a list of breakdown scorecards
            sysparm_breakdown_relation: sys_id of the breakdown relation for a list of related breakdown scorecards
            sysparm_elements_filter: sys_id of the elements filter to be applied to the list of breakdown scorecards
            sysparm_display: true/false to return scorecards whose indicators have 'Publish on Scorecards' checked resp. unchecked, 'all' to ignore the value of 'Publish on Scorecards', default to true
            sysparm_favorites: true to return only scorecards that are the user's favorite
            sysparm_key: true to return only scorecards of Key indicators
            sysparm_target: true to return only scorecards that have a target
            sysparm_contains: matches scorecards by name or description, use comma separated list of strings
            sysparm_tags: matches scorecards by tag sysids, use comma separated list of sysids
            sysparm_per_page: pages by this amount, defaults to 10, maximum is 100
            sysparm_page: page of list of scorecards, defaults to 1
            sysparm_sortby: field to sort by, one of VALUE, CHANGE, CHANGEPERC, GAP, GAPPERC, NAME, ORDER, DEFAULT, INDICATOR_GROUP, FREQUENCY, TARGET, DATE, DIRECTION
            sysparm_sortdir: use value ASC to sort in ascending order, use DESC to sort in descending order
            sysparm_display_value: true/false to return display value or reference field, or 'all' to return both, defaults to all
            sysparm_exclude_reference_link: true to not return Link Information for Reference Fields
            sysparm_include_scores: true to include scores for each scorecard, requires the presence of sysparm_uuid
            sysparm_from: start date in ISO-8601 format for which to return scores
            sysparm_to: end date in ISO-8601 format for which to return scores
            sysparm_step: positive integer indicating the gap between score dates, default to 1
            sysparm_limit: maximum number of scores to return, -1 means to return all scores, default to -1
            sysparm_include_available_breakdowns: true to include all available breakdowns for a scorecard
            sysparm_include_available_aggregates: true to include all available aggregates for a scorecard
            sysparm_include_realtime: true to include the realtime score
            sysparm_include_target_color_scheme: true to include the target color scheme
            sysparm_include_forecast_scores: true to include the forecast scores
            sysparm_include_trendline_scores: true to include the trendline scores
            sysparm_include_prediction_interval: true to include the prediction intervals at default 95%
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/pa/scorecards")
        params = self._build_params(
            sysparm_uuid=sysparm_uuid,
            sysparm_breakdown=sysparm_breakdown,
            sysparm_breakdown_relation=sysparm_breakdown_relation,
            sysparm_elements_filter=sysparm_elements_filter,
            sysparm_display=sysparm_display,
            sysparm_favorites=sysparm_favorites,
            sysparm_key=sysparm_key,
            sysparm_target=sysparm_target,
            sysparm_contains=sysparm_contains,
            sysparm_tags=sysparm_tags,
            sysparm_per_page=sysparm_per_page,
            sysparm_page=sysparm_page,
            sysparm_sortby=sysparm_sortby,
            sysparm_sortdir=sysparm_sortdir,
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_include_scores=sysparm_include_scores,
            sysparm_from=sysparm_from,
            sysparm_to=sysparm_to,
            sysparm_step=sysparm_step,
            sysparm_limit=sysparm_limit,
            sysparm_include_available_breakdowns=sysparm_include_available_breakdowns,
            sysparm_include_available_aggregates=sysparm_include_available_aggregates,
            sysparm_include_realtime=sysparm_include_realtime,
            sysparm_include_target_color_scheme=sysparm_include_target_color_scheme,
            sysparm_include_forecast_scores=sysparm_include_forecast_scores,
            sysparm_include_trendline_scores=sysparm_include_trendline_scores,
            sysparm_include_prediction_interval=sysparm_include_prediction_interval,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_pa_scorecards(
        self, data: Dict[str, Any], sysparm_uuid: Optional[str] = None
    ) -> ServiceNowResponse:
        """Select a Scorecard as favorite
        Args:
            data: Request body data
            sysparm_uuid: The identification of the scorecard the user wants to select as a favorite
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/pa/scorecards")
        params = self._build_params(sysparm_uuid=sysparm_uuid)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_pa_scorecards(
        self, sysparm_uuid: Optional[str] = None
    ) -> ServiceNowResponse:
        """Deselect a Scorecard as favorite
        Args:
            sysparm_uuid: The identification of the scorecard the user wants to deselect as a favorite
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/pa/scorecards")
        params = self._build_params(sysparm_uuid=sysparm_uuid)

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_shortcut(self, sysparm_key: str) -> ServiceNowResponse:
        """
        Args:
            sysparm_key: Shortcut key
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg/shortcut")
        params = self._build_params(sysparm_key=sysparm_key)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_custommatch(
        self, sysparm_term: str, sysparm_search_context_config_id: str
    ) -> ServiceNowResponse:
        """Custom match search API. Search through custom matchers to find a single match.
        Args:
            sysparm_term: The keyword to search for
            sysparm_search_context_config_id: The sys_id of Search Application Context Config to use
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/custommatch")
        params = self._build_params(
            sysparm_term=sysparm_term,
            sysparm_search_context_config_id=sysparm_search_context_config_id,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_exactmatch(
        self, sysparm_term: str, sysparm_search_context_config_id: str
    ) -> ServiceNowResponse:
        """Exact match search API. Search through number field of all qualifying tables for term.
        Args:
            sysparm_term: The keyword to search for
            sysparm_search_context_config_id: The sys_id of Search Application Context Config to use
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/exactmatch")
        params = self._build_params(
            sysparm_term=sysparm_term,
            sysparm_search_context_config_id=sysparm_search_context_config_id,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_itemlookup(
        self, sysparm_table_name: str, sysparm_item_sys_id: str
    ) -> ServiceNowResponse:
        """Item lookup search API. Lookup item based on table name and item sys_id.
        Args:
            sysparm_table_name: The table name to look up
            sysparm_item_sys_id: The item sys_id to look up
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/itemlookup")
        params = self._build_params(
            sysparm_table_name=sysparm_table_name,
            sysparm_item_sys_id=sysparm_item_sys_id,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_sources(
        self, sysparm_search_context_config_id: Optional[str] = None
    ) -> ServiceNowResponse:
        """Get search sources of an application
        Args:
            sysparm_search_context_config_id: Search context config id
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/sources")
        params = self._build_params(
            sysparm_search_context_config_id=sysparm_search_context_config_id
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sources_textsearch(
        self,
        sysparm_term: str,
        sysparm_limit: Optional[str] = None,
        sysparm_page: Optional[str] = None,
        sysparm_search_sources: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Perform text search against multiple search sources using provided term without interleaving
        Args:
            sysparm_term: The keyword(s) to search for
            sysparm_limit: Max number of records to returns for each search source
            sysparm_page: Page of records to return
            sysparm_search_sources: Search sources to search against
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/sources/textsearch")
        params = self._build_params(
            sysparm_term=sysparm_term,
            sysparm_limit=sysparm_limit,
            sysparm_page=sysparm_page,
            sysparm_search_sources=sysparm_search_sources,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sources_textsearch_sys_id(
        self,
        sys_id: str,
        sysparm_term: str,
        sysparm_limit: Optional[str] = None,
        sysparm_page: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Perform text search against a single search source using provided term
        Args:
            sys_id: Path parameter
            sysparm_term: The keyword(s) to search for
            sysparm_limit: Max number of records to returns for each search source
            sysparm_page: Page of records to return
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/search/sources/{sys_id}/textsearch")
        params = self._build_params(
            sysparm_term=sysparm_term,
            sysparm_limit=sysparm_limit,
            sysparm_page=sysparm_page,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_textsearch(
        self,
        sysparm_search_context_config_id: str,
        sysparm_term: str,
        sysparm_next_token: Optional[str] = None,
        sysparm_search_sources: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Perform text search using provided term
        Args:
            sysparm_search_context_config_id: Search context config id
            sysparm_term: The keyword(s) to search for
            sysparm_next_token: Token used to retrieve the next set of search results
            sysparm_search_sources: List of search source ids (comma separated)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/textsearch")
        params = self._build_params(
            sysparm_search_context_config_id=sysparm_search_context_config_id,
            sysparm_term=sysparm_term,
            sysparm_next_token=sysparm_next_token,
            sysparm_search_sources=sysparm_search_sources,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sg_custom_map_token(
        self, providerId: str, documentId: str, externalId: str
    ) -> ServiceNowResponse:
        """
        Args:
            providerId: SG Custom map provider Id
            documentId: SG Custom map screenId
            externalId: MappedId externalID
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sg_custom_map/token")
        params = self._build_params(
            providerId=providerId, documentId=documentId, externalId=externalId
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_slack_bots_config(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/slack_bots_config")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_slack_team_name_clientType_externalId(
        self, clientType: str, externalId: str
    ) -> ServiceNowResponse:
        """
        Args:
            clientType: Path parameter
            externalId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/slack_team_name/{clientType}/{externalId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sla_glidestack_action(
        self, action: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            action: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sla/glidestack/{action}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sla_sla_definition_contractSlaIds(
        self, contractSlaIds: str
    ) -> ServiceNowResponse:
        """
        Args:
            contractSlaIds: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sla/sla_definition/{contractSlaIds}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sla_timeline_taskId(self, taskId: str) -> ServiceNowResponse:
        """
        Args:
            taskId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/sla/timeline/{taskId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_snc_entry_point_selector_getAllEntryPointTypes(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/snc_entry_point_selector/getAllEntryPointTypes")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_snc_entry_point_selector_getEntryPointTypeFields(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/snc_entry_point_selector/getEntryPointTypeFields")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_snc_entry_point_selector_validateEntryPointTypeData(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/snc_entry_point_selector/validateEntryPointTypeData")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_help_documents(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/help/documents")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_help_documents_sys_id(self, sys_id: str) -> ServiceNowResponse:
        """Get Embedded help content by sys_id of the embedded_help_content record.
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/help/documents/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_help_feedback(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/help/feedback")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_help_guidance_guidance_id(
        self, guidance_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            guidance_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/help/guidance/{guidance_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_help_interactions_id(
        self, id: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            id: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/help/interactions/{id}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_help_resources(self) -> ServiceNowResponse:
        """Get the help resource from help content table
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/help/resources")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_help_wnstatus(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/help/wnstatus")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sn_exp_framework_context(
        self,
        exp_param_name: Optional[str] = None,
        fallback_variant: Optional[str] = None,
    ) -> ServiceNowResponse:
        """
        Args:
            exp_param_name: Name of the experiment Parameter
            fallback_variant: FallBack Variant in case of the Experiment is not valid or opted out
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sn_exp_framework/context")
        params = self._build_params(
            exp_param_name=exp_param_name, fallback_variant=fallback_variant
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_sn_exp_framework_feedback(
        self, data: Dict[str, Any], exp_param_name: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
            exp_param_name: Name of the experiment Parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sn_exp_framework/feedback")
        params = self._build_params(exp_param_name=exp_param_name)

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_splunk_token(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/splunk_token")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_as_attachment_getAvailability_sys_id(
        self, sys_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/as_attachment/getAvailability/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_sp_suggestions(
        self,
        sysparm_term: str,
        sysparm_sp_portal_id: str,
        sysparm_suggestions_limit: Optional[str] = None,
        sysparm_search_sources: Optional[str] = None,
    ) -> ServiceNowResponse:
        """SERVICE PORTAL API: Get suggestions for a term
        Args:
            sysparm_term: The keyword(s) for suggestions
            sysparm_sp_portal_id: Service portal id
            sysparm_suggestions_limit: Suggestions limit
            sysparm_search_sources: Array of search sources to use. If not provided - uses all scope sources
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/sp_suggestions")
        params = self._build_params(
            sysparm_term=sysparm_term,
            sysparm_sp_portal_id=sysparm_sp_portal_id,
            sysparm_suggestions_limit=sysparm_suggestions_limit,
            sysparm_search_sources=sysparm_search_sources,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_search_suggestions(
        self,
        sysparm_term: str,
        sysparm_search_context_config_id: str,
        sysparm_search_sources: Optional[str] = None,
    ) -> ServiceNowResponse:
        """SCOPED APPLICATION API: Get suggestions for a term
        Args:
            sysparm_term: The keyword(s) for suggestions
            sysparm_search_context_config_id: Search context config id
            sysparm_search_sources: Array of search sources to use. If not provided - uses all scope sources
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/search/suggestions")
        params = self._build_params(
            sysparm_term=sysparm_term,
            sysparm_search_context_config_id=sysparm_search_context_config_id,
            sysparm_search_sources=sysparm_search_sources,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_syntax_editor_cache_token_type(
        self, token_type: str, name: Optional[str] = None
    ) -> ServiceNowResponse:
        """
        Args:
            token_type: Path parameter
            name: Name
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/syntax_editor/cache/{token_type}")
        params = self._build_params(name=name)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_syntax_editor_completions(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/syntax_editor/completions")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_syntax_editor_getReferences(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/syntax_editor/getReferences")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_syntax_editor_intellisense_tableName(
        self, tableName: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            tableName: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/syntax_editor/intellisense/{tableName}")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_table_tableName(
        self,
        tableName: str,
        sysparm_query: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_limit: Optional[str] = None,
        sysparm_offset: Optional[str] = None,
        sysparm_display_value: Optional[str] = None,
        sysparm_no_count: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_suppress_pagination_header: Optional[str] = None,
        sysparm_view: Optional[str] = None,
        sysparm_query_category: Optional[str] = None,
        sysparm_query_no_domain: Optional[str] = None,
        impersonate_user: Optional[str] = None,
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
            impersonate_user: ServiceNow username or sys_id to impersonate (requires impersonator role)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/table/{tableName}")
        params = self._build_params(
            sysparm_query=sysparm_query,
            sysparm_fields=sysparm_fields,
            sysparm_limit=sysparm_limit,
            sysparm_offset=sysparm_offset,
            sysparm_display_value=sysparm_display_value,
            sysparm_no_count=sysparm_no_count,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_suppress_pagination_header=sysparm_suppress_pagination_header,
            sysparm_view=sysparm_view,
            sysparm_query_category=sysparm_query_category,
            sysparm_query_no_domain=sysparm_query_no_domain,
        )

        request_headers = self.client.headers.copy()

        if impersonate_user:
            request_headers["X-UserToken"] = impersonate_user

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=request_headers,
            query=params
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
        sysparm_view: Optional[str] = None,
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
        params = self._build_params(
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_fields=sysparm_fields,
            sysparm_input_display_value=sysparm_input_display_value,
            sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field,
            sysparm_view=sysparm_view,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
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
        sysparm_query_no_domain: Optional[str] = None,
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
        params = self._build_params(
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_fields=sysparm_fields,
            sysparm_view=sysparm_view,
            sysparm_query_no_domain=sysparm_query_no_domain,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
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
        sysparm_query_no_domain: Optional[str] = None,
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
        params = self._build_params(
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_fields=sysparm_fields,
            sysparm_input_display_value=sysparm_input_display_value,
            sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field,
            sysparm_view=sysparm_view,
            sysparm_query_no_domain=sysparm_query_no_domain,
        )

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_now_table_tableName_sys_id(
        self, tableName: str, sys_id: str, sysparm_query_no_domain: Optional[str] = None
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
            method="DELETE", url=url, headers=self.client.headers, query_params=params
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
        sysparm_query_no_domain: Optional[str] = None,
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
        params = self._build_params(
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_fields=sysparm_fields,
            sysparm_input_display_value=sysparm_input_display_value,
            sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field,
            sysparm_view=sysparm_view,
            sysparm_query_no_domain=sysparm_query_no_domain,
        )

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_now_table_batch_api(self, data: Dict[str, Any]) -> ServiceNowResponse:
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
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_table_batch_api(
        self, data: Dict[str, Any]
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
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_template_spokes_details(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/template_spokes_details")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_timeago_absolute(self) -> ServiceNowResponse:
        """Converts TimeAgo relative date/time to absolute date/time for UI DateTime Filter
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/timeago/absolute")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_tracked_config_files_compare_by_change_time(
        self,
    ) -> ServiceNowResponse:
        """Compare contents of tracked config file around time of change
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/tracked_config_files/compare_by_change_time")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_tracked_config_files_compare_by_two_times(self) -> ServiceNowResponse:
        """Compare two contents of tracekd configuration file by 2 given times
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/tracked_config_files/compare_by_two_times")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_tracked_config_files_diff_audit(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/tracked_config_files/diff_audit")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_tracked_config_files_get_file_content(self) -> ServiceNowResponse:
        """Get content of tracked configuration file at specified time
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/tracked_config_files/get_file_content")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_uaappenginelicensingservice_getAppEngineLicensingStats(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            "/uaappenginelicensingservice/getAppEngineLicensingStats"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_uaappenginelicensingservice_getLicensingRolesStats(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/uaappenginelicensingservice/getLicensingRolesStats")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_uaappenginelicensingservice_getLicensingUserStats(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/uaappenginelicensingservice/getLicensingUserStats")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_now_ua_gcf(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ua_gcf")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ua_gcf_postAnalytic(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/ua_gcf/postAnalytic")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_uiextension_name(
        self,
        name: str,
        sysparm_scope: Optional[str] = None,
        sysparm_variables: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Get the HTML output from running a UIExtensionPoint
        Args:
            name: Path parameter
            sysparm_scope: The scope of the extension point
            sysparm_variables: A JSON object with key:value pairs to pass as variables into the macro
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/uiextension/{name}")
        params = self._build_params(
            sysparm_scope=sysparm_scope, sysparm_variables=sysparm_variables
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ui_glideRecord_tableName(
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
        sysparm_orderBy: Optional[str] = None,
        sysparm_orderByDesc: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Query records from a table
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
            sysparm_orderBy: Specify a column to be used for the sort order of results in ascending
            sysparm_orderByDesc: Specify a column to be used for the sort order of results in descending
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ui/glideRecord/{tableName}")
        params = self._build_params(
            sysparm_query=sysparm_query,
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_suppress_pagination_header=sysparm_suppress_pagination_header,
            sysparm_fields=sysparm_fields,
            sysparm_limit=sysparm_limit,
            sysparm_view=sysparm_view,
            sysparm_query_category=sysparm_query_category,
            sysparm_orderBy=sysparm_orderBy,
            sysparm_orderByDesc=sysparm_orderByDesc,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_ui_glideRecord_tableName(
        self,
        tableName: str,
        data: Dict[str, Any],
        sysparm_input_display_value: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Update multiple record
        Args:
            tableName: Path parameter
            data: Request body data
            sysparm_input_display_value: Set field values using their display value (true) or actual value (false) (default: false)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ui/glideRecord/{tableName}")
        params = self._build_params(
            sysparm_input_display_value=sysparm_input_display_value
        )

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_ui_glideRecord_tableName(
        self,
        tableName: str,
        data: Dict[str, Any],
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_input_display_value: Optional[str] = None,
        sysparm_suppress_auto_sys_field: Optional[str] = None,
        sysparm_view: Optional[str] = None,
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
        url = self._build_url(f"/ui/glideRecord/{tableName}")
        params = self._build_params(
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_fields=sysparm_fields,
            sysparm_input_display_value=sysparm_input_display_value,
            sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field,
            sysparm_view=sysparm_view,
        )

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_ui_glideRecord_tableName_sys_id(
        self,
        tableName: str,
        sys_id: str,
        sysparm_display_value: Optional[str] = None,
        sysparm_exclude_reference_link: Optional[str] = None,
        sysparm_fields: Optional[str] = None,
        sysparm_view: Optional[str] = None,
    ) -> ServiceNowResponse:
        """Retrieve a record
        Args:
            tableName: Path parameter
            sys_id: Path parameter
            sysparm_display_value: Return field display values (true), actual values (false), or both (all) (default: false)
            sysparm_exclude_reference_link: True to exclude Table API links for reference fields (default: false)
            sysparm_fields: A comma-separated list of fields to return in the response
            sysparm_view: Render the response according to the specified UI view (overridden by sysparm_fields)
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ui/glideRecord/{tableName}/{sys_id}")
        params = self._build_params(
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_fields=sysparm_fields,
            sysparm_view=sysparm_view,
        )

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_ui_glideRecord_tableName_sys_id(
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
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ui/glideRecord/{tableName}/{sys_id}")
        params = self._build_params(
            sysparm_display_value=sysparm_display_value,
            sysparm_exclude_reference_link=sysparm_exclude_reference_link,
            sysparm_fields=sysparm_fields,
            sysparm_input_display_value=sysparm_input_display_value,
            sysparm_suppress_auto_sys_field=sysparm_suppress_auto_sys_field,
            sysparm_view=sysparm_view,
        )

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def delete_ui_glideRecord_tableName_sys_id(
        self, tableName: str, sys_id: str
    ) -> ServiceNowResponse:
        """Delete a record
        Args:
            tableName: Path parameter
            sys_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/ui/glideRecord/{tableName}/{sys_id}")
        params = {}

        request = HTTPRequest(
            method="DELETE", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_now_va_bot_uninstall_clientType_externalId(
        self, clientType: str, externalId: str
    ) -> ServiceNowResponse:
        """
        Args:
            clientType: Path parameter
            externalId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/va_bot_uninstall/{clientType}/{externalId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_va_web_client_settings_get_va_web_client_settings(
        self,
    ) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/va_web_client_settings/get_va_web_client_settings")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_virtual_agent_design_intent(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/virtual_agent_design/intent")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_virtual_agent_design_model(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/virtual_agent_design/model")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_model_topics_modelId_language(
        self, modelId: str, language: str
    ) -> ServiceNowResponse:
        """
        Args:
            modelId: Path parameter
            language: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/virtual_agent_design/model/{modelId}/{language}/topics"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_virtual_agent_design_model_status_modelId(
        self, modelId: str
    ) -> ServiceNowResponse:
        """
        Args:
            modelId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/virtual_agent_design/model_status/{modelId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_virtual_agent_design_models(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/virtual_agent_design/models")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_model_validate_modelId(
        self, modelId: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            modelId: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/virtual_agent_design/nlu/model/{modelId}/validate")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_virtual_agent_design_request_translation(
        self, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/virtual_agent_design/request_translation")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_virtual_agent_design_skills(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/virtual_agent_design/skills")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_virtual_agent_design_translation_status_topicId(
        self, topicId: str
    ) -> ServiceNowResponse:
        """
        Args:
            topicId: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/virtual_agent_design/translation_status/{topicId}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_virtual_agent_design_preferences_userId(
        self, userId: str, data: Dict[str, Any]
    ) -> ServiceNowResponse:
        """
        Args:
            userId: Path parameter
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/virtual_agent_design/{userId}/preferences")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_sn_app_wfstudio_getchartdata(self) -> ServiceNowResponse:
        """
        Args:
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/sn_app_wfstudio/getchartdata")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_workspace_administration_workspace_acl_workspace_id(
        self, workspace_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            workspace_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/workspace_administration/workspace_acl/{workspace_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_workspace_administration_workspace_url_workspace_id(
        self, workspace_id: str
    ) -> ServiceNowResponse:
        """
        Args:
            workspace_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/workspace_administration/workspace_url/{workspace_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_wrapup_code(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Create Wrap Up Codes
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/wrapup/code")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_wrapup_code_code_id(self, code_id: str) -> ServiceNowResponse:
        """Retrieve Wrap Up Code by ID
        Args:
            code_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/wrapup/code/{code_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_agent_interaction_agent_id_interaction_id(
        self, agent_id: str, interaction_id: str
    ) -> ServiceNowResponse:
        """Retrieve Segment by Agent ID and Interaction ID
        Args:
            agent_id: Path parameter
            interaction_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(
            f"/v1/wrapup/segment/agent/{agent_id}/interaction/{interaction_id}"
        )
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def post_segment_create(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Create Wrap Up Segment
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/wrapup/segment/create")
        params = {}

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def put_segment_update(self, data: Dict[str, Any]) -> ServiceNowResponse:
        """Update Wrap Up Segment
        Args:
            data: Request body data
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url("/v1/wrapup/segment/update")
        params = {}

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=self.client.headers,
            query_params=params,
            body=data,
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    async def get_wrapup_segment_segment_id(
        self, segment_id: str
    ) -> ServiceNowResponse:
        """Retrieve Segment by Sys ID/External Segment ID
        Args:
            segment_id: Path parameter
        Returns:
            ServiceNowResponse object with success status and data/error"""
        url = self._build_url(f"/v1/wrapup/segment/{segment_id}")
        params = {}

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)
        return await self._handle_response(response)

    # =========================================================================
    # CUSTOM METHODS FOR KB CONNECTOR
    # =========================================================================

    async def get_kb_knowledge_by_id(
        self, sys_id: str, fields: Optional[List[str]] = None
    ) -> ServiceNowResponse:
        """
        Get a single KB article by sys_id with specified fields.

        Args:
            sys_id: The sys_id of the KB article
            fields: List of fields to return (e.g., ['sys_id', 'short_description', 'text'])

        Returns:
            ServiceNowResponse object with article data
        """
        url = self._build_url(f"/table/kb_knowledge/{sys_id}")
        params = {}

        if fields:
            params["sysparm_fields"] = ",".join(fields)

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params=params
        )
        response = await self.client.execute(request)

        # Parse the response
        try:
            if response.status >= HttpStatusCode.BAD_REQUEST.value:
                return ServiceNowResponse(
                    success=False,
                    error=f"HTTP {response.status}",
                    message=response.text(),
                )

            data = response.json() if response.text() else {}
            result_data = data.get("result", {})

            return ServiceNowResponse(
                success=True,
                records=[result_data] if result_data else [],
                data=data
            )
        except Exception as e:
            return ServiceNowResponse(
                success=False, error=str(e), message="Failed to parse response"
            )

    async def download_attachment(self, attachment_sys_id: str) -> bytes:
        """
        Download attachment file content from ServiceNow.

        Args:
            attachment_sys_id: The sys_id of the attachment

        Returns:
            bytes: Binary file content

        Raises:
            Exception: If download fails
        """
        url = self._build_url(f"/attachment/{attachment_sys_id}/file")

        request = HTTPRequest(
            method="GET", url=url, headers=self.client.headers, query_params={}
        )
        response = await self.client.execute(request)

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise Exception(f"Failed to download attachment: HTTP {response.status}")

        # Return binary content
        return response.response.content
