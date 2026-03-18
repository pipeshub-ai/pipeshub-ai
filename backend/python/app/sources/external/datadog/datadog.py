# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import Any, Dict, Optional, Union

from datadog_api_client import ApiClient, Configuration  # type: ignore[reportMissingImports]
from datadog_api_client.v1.api.dashboards_api import DashboardsApi  # type: ignore[reportMissingImports]
from datadog_api_client.v1.api.monitors_api import MonitorsApi  # type: ignore[reportMissingImports]
from datadog_api_client.v1.api.hosts_api import HostsApi  # type: ignore[reportMissingImports]
from datadog_api_client.v1.api.metrics_api import MetricsApi as MetricsApiV1  # type: ignore[reportMissingImports]
from datadog_api_client.v1.api.synthetics_api import SyntheticsApi  # type: ignore[reportMissingImports]
from datadog_api_client.v1.api.downtimes_api import DowntimesApi  # type: ignore[reportMissingImports]
from datadog_api_client.v1.model.dashboard import Dashboard  # type: ignore[reportMissingImports]
from datadog_api_client.v1.model.monitor import Monitor  # type: ignore[reportMissingImports]
from datadog_api_client.v1.model.monitor_update_request import MonitorUpdateRequest  # type: ignore[reportMissingImports]
from datadog_api_client.v2.api.users_api import UsersApi  # type: ignore[reportMissingImports]
from datadog_api_client.v2.api.incidents_api import IncidentsApi  # type: ignore[reportMissingImports]
from datadog_api_client.v2.api.logs_api import LogsApi  # type: ignore[reportMissingImports]
from datadog_api_client.v2.api.metrics_api import MetricsApi as MetricsApiV2  # type: ignore[reportMissingImports]
from datadog_api_client.v2.api.service_definition_api import ServiceDefinitionApi  # type: ignore[reportMissingImports]
from datadog_api_client.v2.model.logs_list_request import LogsListRequest  # type: ignore[reportMissingImports]
from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter  # type: ignore[reportMissingImports]
from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage  # type: ignore[reportMissingImports]
from datadog_api_client.v2.model.logs_sort import LogsSort  # type: ignore[reportMissingImports]

from app.sources.client.datadog.datadog import DatadogResponse


class DatadogDataSource:
    """
    Typed wrapper over the official datadog-api-client SDK for common
    Datadog business operations.

    Accepts either a ``Configuration`` instance *or* any object with
    ``.get_sdk() -> Configuration``.
    """

    def __init__(self, client_or_config: Union[Configuration, object]) -> None:
        if hasattr(client_or_config, "get_sdk"):
            self._config: Configuration = getattr(client_or_config, "get_sdk")()
        else:
            self._config = client_or_config  # type: ignore[assignment]

    # ---- helpers ----

    @staticmethod
    def _to_dict_safe(obj: Any) -> Any:
        """Convert SDK response objects to dicts when possible."""
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[reportUnknownMemberType]
        if isinstance(obj, list):
            out: list[Any] = []
            for item in obj:  # type: ignore[reportUnknownVariableType]
                out.append(item.to_dict() if hasattr(item, "to_dict") else item)  # type: ignore[reportUnknownMemberType]
            return out
        return obj

    @staticmethod
    def _params(**kwargs: Any) -> Dict[str, Any]:
        """Filter out None values to avoid overriding SDK defaults."""
        return {k: v for k, v in kwargs.items() if v is not None}
    def list_dashboards(self) -> DatadogResponse:
        """List all dashboards.  [dashboards]"""
        try:
            with ApiClient(self._config) as api_client:
                api = DashboardsApi(api_client)
                result = api.list_dashboards()
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def get_dashboard(self, dashboard_id: str) -> DatadogResponse:
        """Get a single dashboard by ID.  [dashboards]"""
        try:
            with ApiClient(self._config) as api_client:
                api = DashboardsApi(api_client)
                result = api.get_dashboard(dashboard_id=dashboard_id)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def create_dashboard(self, body: Dict[str, Any]) -> DatadogResponse:
        """Create a dashboard. Pass the dashboard definition as a dict.  [dashboards]"""
        try:
            with ApiClient(self._config) as api_client:
                api = DashboardsApi(api_client)
                dashboard = Dashboard(**body)
                result = api.create_dashboard(body=dashboard)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_monitors(self, group_states: Optional[str] = None, name: Optional[str] = None, tags: Optional[str] = None, monitor_tags: Optional[str] = None, page: Optional[int] = None, page_size: Optional[int] = None) -> DatadogResponse:
        """List monitors with optional filters.  [monitors]"""
        try:
            with ApiClient(self._config) as api_client:
                api = MonitorsApi(api_client)
                kwargs = self._params(group_states=group_states, name=name, tags=tags, monitor_tags=monitor_tags, page=page, page_size=page_size)
                result = api.list_monitors(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def get_monitor(self, monitor_id: int) -> DatadogResponse:
        """Get a single monitor by ID.  [monitors]"""
        try:
            with ApiClient(self._config) as api_client:
                api = MonitorsApi(api_client)
                result = api.get_monitor(monitor_id=monitor_id)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def create_monitor(self, body: Dict[str, Any]) -> DatadogResponse:
        """Create a monitor. Pass the monitor definition as a dict.  [monitors]"""
        try:
            with ApiClient(self._config) as api_client:
                api = MonitorsApi(api_client)
                monitor = Monitor(**body)
                result = api.create_monitor(body=monitor)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def update_monitor(self, monitor_id: int, body: Dict[str, Any]) -> DatadogResponse:
        """Update an existing monitor.  [monitors]"""
        try:
            with ApiClient(self._config) as api_client:
                api = MonitorsApi(api_client)
                update_req = MonitorUpdateRequest(**body)
                result = api.update_monitor(monitor_id=monitor_id, body=update_req)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def delete_monitor(self, monitor_id: int) -> DatadogResponse:
        """Delete a monitor by ID.  [monitors]"""
        try:
            with ApiClient(self._config) as api_client:
                api = MonitorsApi(api_client)
                result = api.delete_monitor(monitor_id=monitor_id)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_users(self, page_size: Optional[int] = None, page_number: Optional[int] = None, sort: Optional[str] = None, sort_dir: Optional[str] = None, filter_str: Optional[str] = None, filter_status: Optional[str] = None) -> DatadogResponse:
        """List all users in the organization.  [users]"""
        try:
            with ApiClient(self._config) as api_client:
                api = UsersApi(api_client)
                kwargs: Dict[str, Any] = {}
                if page_size is not None:
                    kwargs['page_size'] = page_size
                if page_number is not None:
                    kwargs['page_number'] = page_number
                if sort is not None:
                    kwargs['sort'] = sort
                if sort_dir is not None:
                    kwargs['sort_dir'] = sort_dir
                if filter_str is not None:
                    kwargs['filter'] = filter_str
                if filter_status is not None:
                    kwargs['filter_status'] = filter_status
                result = api.list_users(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def get_user(self, user_id: str) -> DatadogResponse:
        """Get a single user by ID.  [users]"""
        try:
            with ApiClient(self._config) as api_client:
                api = UsersApi(api_client)
                result = api.get_user(user_id=user_id)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_hosts(self, filter_str: Optional[str] = None, sort_field: Optional[str] = None, sort_dir: Optional[str] = None, start: Optional[int] = None, count: Optional[int] = None, from_ts: Optional[int] = None) -> DatadogResponse:
        """List all hosts for the organization.  [hosts]"""
        try:
            with ApiClient(self._config) as api_client:
                api = HostsApi(api_client)
                kwargs: Dict[str, Any] = {}
                if filter_str is not None:
                    kwargs['filter'] = filter_str
                if sort_field is not None:
                    kwargs['sort_field'] = sort_field
                if sort_dir is not None:
                    kwargs['sort_dir'] = sort_dir
                if start is not None:
                    kwargs['start'] = start
                if count is not None:
                    kwargs['count'] = count
                if from_ts is not None:
                    kwargs['_from'] = from_ts
                result = api.list_hosts(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def query_timeseries(self, from_ts: int, to_ts: int, query: str) -> DatadogResponse:
        """Query timeseries data using a metrics query string.  [metrics]"""
        try:
            with ApiClient(self._config) as api_client:
                api = MetricsApiV1(api_client)
                result = api.query_metrics(_from=from_ts, to=to_ts, query=query)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_active_metrics(self, filter_configured: Optional[bool] = None, filter_tags_configured: Optional[str] = None, filter_metric_type: Optional[str] = None, filter_include_percentiles: Optional[bool] = None, filter_queried: Optional[bool] = None, filter_tags: Optional[str] = None, window_seconds: Optional[int] = None, page_size: Optional[int] = None, page_cursor: Optional[str] = None) -> DatadogResponse:
        """List active metric tag configurations with optional filters.  [metrics]"""
        try:
            with ApiClient(self._config) as api_client:
                api = MetricsApiV2(api_client)
                kwargs = self._params(
                    filter_configured=filter_configured,
                    filter_tags_configured=filter_tags_configured,
                    filter_metric_type=filter_metric_type,
                    filter_include_percentiles=filter_include_percentiles,
                    filter_queried=filter_queried,
                    filter_tags=filter_tags,
                    window_seconds=window_seconds,
                    page_size=page_size,
                    page_cursor=page_cursor,
                )
                result = api.list_tag_configurations(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_incidents(self, page_size: Optional[int] = None, page_offset: Optional[int] = None) -> DatadogResponse:
        """List all incidents.  [incidents]"""
        try:
            with ApiClient(self._config) as api_client:
                api = IncidentsApi(api_client)
                kwargs = self._params(page_size=page_size, page_offset=page_offset)
                result = api.list_incidents(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def get_incident(self, incident_id: str) -> DatadogResponse:
        """Get a single incident by ID.  [incidents]"""
        try:
            with ApiClient(self._config) as api_client:
                api = IncidentsApi(api_client)
                result = api.get_incident(incident_id=incident_id)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def search_logs(self, filter_query: Optional[str] = None, filter_from: Optional[str] = None, filter_to: Optional[str] = None, sort: Optional[str] = None, page_cursor: Optional[str] = None, page_limit: Optional[int] = None) -> DatadogResponse:
        """Search and filter logs.  [logs]"""
        try:
            with ApiClient(self._config) as api_client:
                api = LogsApi(api_client)
                filter_obj = LogsQueryFilter()
                if filter_query is not None:
                    filter_obj.query = filter_query
                if filter_from is not None:
                    filter_obj._from = filter_from
                if filter_to is not None:
                    filter_obj.to = filter_to
                body_kwargs: Dict[str, Any] = {'filter': filter_obj}
                if sort is not None:
                    body_kwargs['sort'] = LogsSort(sort)
                if page_cursor is not None or page_limit is not None:
                    page_obj = LogsListRequestPage()
                    if page_cursor is not None:
                        page_obj.cursor = page_cursor
                    if page_limit is not None:
                        page_obj.limit = page_limit
                    body_kwargs['page'] = page_obj
                request_body = LogsListRequest(**body_kwargs)
                result = api.list_logs(body=request_body)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_synthetics_tests(self, page_size: Optional[int] = None, page_number: Optional[int] = None) -> DatadogResponse:
        """List all Synthetics tests.  [synthetics]"""
        try:
            with ApiClient(self._config) as api_client:
                api = SyntheticsApi(api_client)
                kwargs = self._params(page_size=page_size, page_number=page_number)
                result = api.list_tests(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def get_synthetics_test(self, public_id: str) -> DatadogResponse:
        """Get a single Synthetics test by public ID.  [synthetics]"""
        try:
            with ApiClient(self._config) as api_client:
                api = SyntheticsApi(api_client)
                result = api.get_test(public_id=public_id)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_downtimes(self, current_only: Optional[bool] = None, with_creator: Optional[bool] = None) -> DatadogResponse:
        """List all scheduled downtimes.  [downtimes]"""
        try:
            with ApiClient(self._config) as api_client:
                api = DowntimesApi(api_client)
                kwargs = self._params(current_only=current_only, with_creator=with_creator)
                result = api.list_downtimes(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))
    def list_service_definitions(self, page_size: Optional[int] = None, page_number: Optional[int] = None, schema_version: Optional[str] = None) -> DatadogResponse:
        """List all service definitions.  [service definitions]"""
        try:
            with ApiClient(self._config) as api_client:
                api = ServiceDefinitionApi(api_client)
                kwargs = self._params(page_size=page_size, page_number=page_number, schema_version=schema_version)
                result = api.list_service_definitions(**kwargs)
                return DatadogResponse(success=True, data=self._to_dict_safe(result))
        except Exception as e:
            return DatadogResponse(success=False, error=str(e))

