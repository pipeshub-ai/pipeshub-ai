    # Auto-generated Microsoft Planner API client.
    # ruff: noqa: BLE001, D417, E501, ANN003, PGH003, PLR0912, C901, PLR0913, G004, TRY400, TRY003, EM101, D100, INP001, PLR0915, D401

import keyword as _kw
import logging
import re
from collections.abc import Mapping

from app.sources.client.microsoft.microsoft import MSGraphClient, MSGraphResponse

# Set up logger
logger = logging.getLogger(__name__)

class PlannerDataSource:
    """Auto-generated Microsoft Planner API client wrapper.

    - Uses Microsoft Graph SDK client internally
    - Snake_case method names for all Microsoft Planner API operations
    - Standardized MSGraphResponse format for all responses
    - No direct HTTP calls - all requests go through Graph SDK
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client."""
        self.client = client.get_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Microsoft Planner API client initialized successfully")

    def _sanitize_py_name(self, name: str) -> str:
        """Runtime-safe version of sanitize_py_name for mirroring param keys.

        Turns '$select' -> 'dollar_select', 'message-id' -> 'message_id', etc.
        """
        n = name.replace("$", "dollar_").replace("@", "at_").replace(".", "_")
        n = re.sub(r"[^0-9a-zA-Z_]", "_", n)
        if n and n[0].isdigit():
            n = f"_{n}"
        if _kw.iskeyword(n):
            n += "_"
        if n.startswith("__"):
            n = f"_{n}"
        return n

    def _handle_response(self, response: dict[str, str | int | bool | list[object]]) -> MSGraphResponse:
        """Handle Microsoft Graph API response."""
        try:
            if response is None:
                    return MSGraphResponse(success=False, error="Empty response from Microsoft Graph")

            success = True
            error_msg = None

            # Handle error responses
            if hasattr(response, "error"):
                success = False
                error_msg = str(response.error) #type:ignore
            elif isinstance(response, dict) and "error" in response:
                success = False
                error_msg = str(response["error"])
            elif hasattr(response, "code") and hasattr(response, "message"):
                success = False
                error_msg = f"{response.code}: {response.message}" #type:ignore

            return MSGraphResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Microsoft Planner API response: {e}")
            return MSGraphResponse(success=False, error=str(e))

    def get_data_source(self) -> "PlannerDataSource":
        """Get the underlying Microsoft Planner API client."""
        return self


    def me_delete_planner(self, *, if_match: str | None = None, **kwargs) -> MSGraphResponse:
        """Delete navigation property planner for me.

        Microsoft Planner API method: /me/planner (DELETE).

        Args:
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().delete(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_get_planner(self,
        *,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get planner from me.

        Microsoft Planner API method: /me/planner (GET).

        Args:
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_update_planner(self,
        *,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property planner in me.

        Microsoft Planner API method: /me/planner (PATCH).

        Args:
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner()  # type:ignore
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_create_plans(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create new navigation property to plans for me.

        Microsoft Planner API method: /me/planner/plans (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_create_tasks(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create new navigation property to tasks for me.

        Microsoft Planner API method: /me/planner/tasks (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_delete_plans(self,
        *,
        planner_plan_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property plans for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_delete_tasks(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property tasks for me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id} (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_get_plans(self,
        *,
        planner_plan_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plans from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_get_tasks(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id} (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_list_plans(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List plans.

        Microsoft Planner API method: /me/planner/plans (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_list_tasks(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List tasks.

        Microsoft Planner API method: /me/planner/tasks (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_update_plans(self,
        *,
        planner_plan_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property plans in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_update_tasks(self,
        *,
        planner_task_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property tasks in me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id} (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_create_buckets(self,
        *,
        planner_plan_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to buckets for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets (POST).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_create_tasks(self,
        *,
        planner_plan_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to tasks for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks (POST).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_delete_buckets(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property buckets for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_delete_details(self,
        *,
        planner_plan_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/details (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_delete_tasks(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property tasks for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_get_buckets(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get buckets from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_get_count_036a(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /me/planner/plans/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_get_details(self,
        *,
        planner_plan_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get details from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/details (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_get_tasks(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_list_buckets(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get buckets from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_list_tasks(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_update_buckets(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property buckets in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_update_details(self,
        *,
        planner_plan_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property details in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/details (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).details()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_update_tasks(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property tasks in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_create_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to tasks for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks (POST).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_delete_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property tasks for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_get_count_3740(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/$count (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_get_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_list_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_update_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property tasks in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_delete_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property assignedToTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_delete_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property bucketTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_delete_details(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_delete_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property progressTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_get_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get assignedToTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_get_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bucketTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_get_count_2767(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/$count (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_get_details(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get details from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_get_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get progressTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_update_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property assignedToTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_update_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property bucketTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_update_details(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property details in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_buckets_tasks_update_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property progressTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_delete_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property assignedToTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .assigned_to_task_board_format().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_delete_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property bucketTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .bucket_task_board_format().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_delete_details(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/details (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_delete_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property progressTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .progress_task_board_format().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_get_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get assignedToTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .assigned_to_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_get_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bucketTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .bucket_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_get_count_d046(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/$count (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_get_details(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get details from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/details (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_get_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get progressTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .progress_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_update_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property assignedToTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .assigned_to_task_board_format().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_update_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property bucketTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .bucket_task_board_format().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_update_details(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property details in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/details (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .details().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_plans_tasks_update_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property progressTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .progress_task_board_format().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_delete_assigned_to_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property assignedToTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/assignedToTaskBoardFormat (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_delete_bucket_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property bucketTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/bucketTaskBoardFormat (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).bucket_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_delete_details(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/details (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_delete_progress_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property progressTaskBoardFormat for me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/progressTaskBoardFormat (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).progress_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_get_assigned_to_task_board_format(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get assignedToTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/assignedToTaskBoardFormat (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_get_bucket_task_board_format(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bucketTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/bucketTaskBoardFormat (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).bucket_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_get_count_5b5d(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /me/planner/tasks/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_get_details(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get details from me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/details (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_get_progress_task_board_format(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get progressTaskBoardFormat from me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/progressTaskBoardFormat (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).progress_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_update_assigned_to_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property assignedToTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/assignedToTaskBoardFormat (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_update_bucket_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property bucketTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/bucketTaskBoardFormat (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).bucket_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_update_details(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property details in me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/details (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).details()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def me_planner_tasks_update_progress_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property progressTaskBoardFormat in me.

        Microsoft Planner API method: /me/planner/tasks/{plannerTask-id}/progressTaskBoardFormat (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).progress_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_create_buckets(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create plannerBucket.

        Microsoft Planner API method: /planner/buckets (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_create_plans(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create plannerPlan.

        Microsoft Planner API method: /planner/plans (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_create_tasks(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create plannerTask.

        Microsoft Planner API method: /planner/tasks (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_delete_buckets(self,
        *,
        planner_bucket_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete plannerBucket.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id} (DELETE).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_delete_plans(self,
        *,
        planner_plan_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete plannerPlan.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_delete_tasks(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete plannerTask.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id} (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_get_buckets(self,
        *,
        planner_bucket_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerBucket.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id} (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_get_plans(self,
        *,
        planner_plan_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerPlan.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_get_tasks(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerTask.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id} (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_list_buckets(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List buckets.

        Microsoft Planner API method: /planner/buckets (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_list_plans(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List plans.

        Microsoft Planner API method: /planner/plans (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_list_tasks(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List plannerTask objects.

        Microsoft Planner API method: /planner/tasks (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_update_buckets(self,
        *,
        planner_bucket_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannerbucket.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id} (PATCH).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_update_plans(self,
        *,
        planner_plan_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannerPlan.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_update_tasks(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannerTask.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id} (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_create_tasks(self,
        *,
        planner_bucket_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to tasks for planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks (POST).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_delete_tasks(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property tasks for planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (DELETE).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_get_count_9ddb(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /planner/buckets/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_get_tasks(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_list_tasks(self,
        *,
        planner_bucket_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List tasks.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_update_tasks(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property tasks in planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (PATCH).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_delete_assigned_to_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property assignedToTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (DELETE).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_delete_bucket_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property bucketTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (DELETE).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_delete_details(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (DELETE).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_delete_progress_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property progressTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (DELETE).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_get_assigned_to_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get assignedToTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_get_bucket_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bucketTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_get_count_145a(self,
        *,
        planner_bucket_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/$count (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_get_details(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get details from planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_get_progress_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get progressTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (GET).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_update_assigned_to_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property assignedToTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (PATCH).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_update_bucket_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property bucketTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (PATCH).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_update_details(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property details in planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (PATCH).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_buckets_tasks_update_progress_task_board_format(self,
        *,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property progressTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (PATCH).

        Args:
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().buckets().by_id(params  # type:ignore
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_planner_get_planner(self,
        *,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get planner.

        Microsoft Planner API method: /planner (GET).

        Args:
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_planner_update_planner(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Update planner.

        Microsoft Planner API method: /planner (PATCH).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner()  # type:ignore
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_create_buckets(self,
        *,
        planner_plan_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to buckets for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets (POST).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_create_tasks(self,
        *,
        planner_plan_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to tasks for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks (POST).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_delete_buckets(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property buckets for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_delete_details(self,
        *,
        planner_plan_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/details (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_delete_tasks(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property tasks for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_get_buckets(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get buckets from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_get_count_e322(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /planner/plans/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_get_details(self,
        *,
        planner_plan_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerPlanDetails.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/details (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_get_tasks(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_list_buckets(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List buckets.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_list_tasks(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List tasks.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_update_buckets(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property buckets in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_update_details(self,
        *,
        planner_plan_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannerplandetails.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/details (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).details()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_update_tasks(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property tasks in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_create_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to tasks for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks (POST).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_delete_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property tasks for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_get_count_240a(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/$count (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_get_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_list_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get tasks from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_update_tasks(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property tasks in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id} (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_delete_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property assignedToTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_delete_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property bucketTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_delete_details(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_delete_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property progressTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_get_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get assignedToTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_get_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bucketTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_get_count_8a6a(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/$count (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_get_details(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get details from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_get_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get progressTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_update_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property assignedToTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_update_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property bucketTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).bucket_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_update_details(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property details in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/details (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).details()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_buckets_tasks_update_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_bucket_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property progressTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/buckets/{plannerBucket-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_bucket_id (required): The unique identifier of plannerBucket
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_bucket_id is not None:
            params["plannerBucket-id"] = planner_bucket_id
            params["plannerBucket_id"] = planner_bucket_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).buckets().by_id(params
            .get("plannerBucket_id", "")).tasks().by_id(params
            .get("plannerTask_id", "")).progress_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_delete_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property assignedToTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .assigned_to_task_board_format().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_delete_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property bucketTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .bucket_task_board_format().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_delete_details(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/details (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_delete_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property progressTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (DELETE).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .progress_task_board_format().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_get_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get assignedToTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .assigned_to_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_get_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bucketTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .bucket_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_get_count_09d1(self,
        *,
        planner_plan_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/$count (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_get_details(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get details from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/details (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_get_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get progressTaskBoardFormat from planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (GET).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .progress_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_update_assigned_to_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property assignedToTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/assignedToTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .assigned_to_task_board_format().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_update_bucket_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property bucketTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/bucketTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .bucket_task_board_format().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_update_details(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property details in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/details (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .details().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_plans_tasks_update_progress_task_board_format(self,
        *,
        planner_plan_id: str,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property progressTaskBoardFormat in planner.

        Microsoft Planner API method: /planner/plans/{plannerPlan-id}/tasks/{plannerTask-id}/progressTaskBoardFormat (PATCH).

        Args:
            planner_plan_id (required): The unique identifier of plannerPlan
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_plan_id is not None:
            params["plannerPlan-id"] = planner_plan_id
            params["plannerPlan_id"] = planner_plan_id
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().plans().by_id(params  # type:ignore
            .get("plannerPlan_id", "")).tasks().by_id(params.get("plannerTask_id", ""))
            .progress_task_board_format().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_delete_assigned_to_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property assignedToTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/assignedToTaskBoardFormat (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_delete_bucket_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property bucketTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/bucketTaskBoardFormat (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).bucket_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_delete_details(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property details for planner.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/details (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).details().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_delete_progress_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property progressTaskBoardFormat for planner.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/progressTaskBoardFormat (DELETE).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).progress_task_board_format()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_get_assigned_to_task_board_format(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerAssignedToTaskBoardTaskFormat.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/assignedToTaskBoardFormat (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_get_bucket_task_board_format(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerBucketTaskBoardTaskFormat.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/bucketTaskBoardFormat (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).bucket_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_get_count_bfd2(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Planner API method: /planner/tasks/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_get_details(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerTaskDetails.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/details (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).details().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_get_progress_task_board_format(self,
        *,
        planner_task_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get plannerProgressTaskBoardTaskFormat.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/progressTaskBoardFormat (GET).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).progress_task_board_format().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_update_assigned_to_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannerAssignedToTaskBoardTaskFormat.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/assignedToTaskBoardFormat (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).assigned_to_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_update_bucket_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannerBucketTaskBoardTaskFormat.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/bucketTaskBoardFormat (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).bucket_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_update_details(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannertaskdetails.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/details (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).details()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )

    def planner_tasks_update_progress_task_board_format(self,
        *,
        planner_task_id: str,
        if_match: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update plannerProgressTaskBoardTaskFormat.

        Microsoft Planner API method: /planner/tasks/{plannerTask-id}/progressTaskBoardFormat (PATCH).

        Args:
            planner_task_id (required): The unique identifier of plannerTask
            if_match (required): ETag value.
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if planner_task_id is not None:
            params["plannerTask-id"] = planner_task_id
            params["plannerTask_id"] = planner_task_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().planner().tasks().by_id(params  # type:ignore
            .get("plannerTask_id", "")).progress_task_board_format()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Planner API API call failed: {e!s}",
            )
