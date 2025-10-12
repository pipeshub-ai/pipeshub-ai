"""Auto-generated DataDog DataSource wrapper.

This module is generated from DataDog SDK API definitions.
All methods return DataDogResponse for consistent error handling.
"""

import logging
from typing import Any

from app.sources.client.datadog.datadog import DataDogClient, DataDogResponse

logger = logging.getLogger(__name__)


class DataDogDataSource:
    """Auto-generated DataDog API client wrapper.

    - Wraps the official DataDog SDK client (DataDogClient)
    - Snake_case method names for Python conventions
    - All responses wrapped in standardized DataDogResponse format
    - Comprehensive logging for debugging

    Generated methods cover:
    - Monitors: list, get, create, update, delete
    - Dashboards: list, get, create, update, delete
    - Logs: list, aggregate
    - Metrics: query, list active
    """

    def __init__(self, client: DataDogClient) -> None:
        """Initialize DataDog DataSource.

        Args:
            client: Initialized DataDogClient instance

        """
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.logger.info("DataDogDataSource initialized")

    def _handle_response(self, response: DataDogResponse, method_name: str) -> DataDogResponse:
        """Handle DataDog client response.

        Args:
            response: Response from DataDogClient
            method_name: Name of the method being called

        Returns:
            DataDogResponse: The response object

        """
        if response.success:
            self.logger.info(f"DataDog API call successful: {method_name}")
        else:
            self.logger.error(f"DataDog API call failed: {method_name} - {response.error}")
        return response


    # ==================== Monitors API ====================

    def list_monitors(self, group_states: str | None = None, name: str | None = None, tags: str | None = None, monitor_tags: str | None = None, with_downtimes: bool | None = False, **kwargs: Any) -> DataDogResponse:
        """List all monitors.

        DataDog SDK method: `list_monitors`

        Args:
            group_states (optional): Comma-separated list of states to filter by
            name (optional): String to filter monitors by name
            tags (optional): Comma-separated list of tags
            monitor_tags (optional): Comma-separated list of monitor tags
            with_downtimes (optional): Include downtime info

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: list_monitors")
        try:
            kwargs_api: dict[str, Any] = {}
            if group_states is not None:
                kwargs_api["group_states"] = group_states
            if name is not None:
                kwargs_api["name"] = name
            if tags is not None:
                kwargs_api["tags"] = tags
            if monitor_tags is not None:
                kwargs_api["monitor_tags"] = monitor_tags
            if with_downtimes is not None:
                kwargs_api["with_downtimes"] = with_downtimes
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.list_monitors(**kwargs_api)
            return self._handle_response(response, "list_monitors")
        except Exception as e:
            self.logger.error(f"DataDog API error in list_monitors: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def get_monitor(self, monitor_id: int, group_states: str | None = None, **kwargs: Any) -> DataDogResponse:
        """Get a monitor by ID.

        DataDog SDK method: `get_monitor`

        Args:
            monitor_id (required): Monitor ID
            group_states (optional): Group states to include

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: get_monitor")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["monitor_id"] = monitor_id
            if group_states is not None:
                kwargs_api["group_states"] = group_states
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.get_monitor(**kwargs_api)
            return self._handle_response(response, "get_monitor")
        except Exception as e:
            self.logger.error(f"DataDog API error in get_monitor: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def create_monitor(self, body: dict, **kwargs: Any) -> DataDogResponse:
        """Create a new monitor.

        DataDog SDK method: `create_monitor`

        Args:
            body (required): Monitor definition

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: create_monitor")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["body"] = body
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.create_monitor(**kwargs_api)
            return self._handle_response(response, "create_monitor")
        except Exception as e:
            self.logger.error(f"DataDog API error in create_monitor: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def update_monitor(self, monitor_id: int, body: dict, **kwargs: Any) -> DataDogResponse:
        """Update an existing monitor.

        DataDog SDK method: `update_monitor`

        Args:
            monitor_id (required): Monitor ID
            body (required): Monitor update definition

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: update_monitor")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["monitor_id"] = monitor_id
            kwargs_api["body"] = body
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.update_monitor(**kwargs_api)
            return self._handle_response(response, "update_monitor")
        except Exception as e:
            self.logger.error(f"DataDog API error in update_monitor: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def delete_monitor(self, monitor_id: int, **kwargs: Any) -> DataDogResponse:
        """Delete a monitor.

        DataDog SDK method: `delete_monitor`

        Args:
            monitor_id (required): Monitor ID to delete

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: delete_monitor")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["monitor_id"] = monitor_id
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.delete_monitor(**kwargs_api)
            return self._handle_response(response, "delete_monitor")
        except Exception as e:
            self.logger.error(f"DataDog API error in delete_monitor: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    # ==================== Dashboards API ====================

    def list_dashboards(self, filter_shared: bool | None = False, filter_deleted: bool | None = False, **kwargs: Any) -> DataDogResponse:
        """List all dashboards.

        DataDog SDK method: `list_dashboards`

        Args:
            filter_shared (optional): Filter shared dashboards
            filter_deleted (optional): Filter deleted dashboards

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: list_dashboards")
        try:
            kwargs_api: dict[str, Any] = {}
            if filter_shared is not None:
                kwargs_api["filter_shared"] = filter_shared
            if filter_deleted is not None:
                kwargs_api["filter_deleted"] = filter_deleted
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.list_dashboards(**kwargs_api)
            return self._handle_response(response, "list_dashboards")
        except Exception as e:
            self.logger.error(f"DataDog API error in list_dashboards: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def get_dashboard(self, dashboard_id: str, **kwargs: Any) -> DataDogResponse:
        """Get a dashboard by ID.

        DataDog SDK method: `get_dashboard`

        Args:
            dashboard_id (required): Dashboard ID

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: get_dashboard")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["dashboard_id"] = dashboard_id
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.get_dashboard(**kwargs_api)
            return self._handle_response(response, "get_dashboard")
        except Exception as e:
            self.logger.error(f"DataDog API error in get_dashboard: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def create_dashboard(self, body: dict, **kwargs: Any) -> DataDogResponse:
        """Create a new dashboard.

        DataDog SDK method: `create_dashboard`

        Args:
            body (required): Dashboard definition

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: create_dashboard")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["body"] = body
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.create_dashboard(**kwargs_api)
            return self._handle_response(response, "create_dashboard")
        except Exception as e:
            self.logger.error(f"DataDog API error in create_dashboard: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def update_dashboard(self, dashboard_id: str, body: dict, **kwargs: Any) -> DataDogResponse:
        """Update an existing dashboard.

        DataDog SDK method: `update_dashboard`

        Args:
            dashboard_id (required): Dashboard ID
            body (required): Dashboard update definition

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: update_dashboard")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["dashboard_id"] = dashboard_id
            kwargs_api["body"] = body
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.update_dashboard(**kwargs_api)
            return self._handle_response(response, "update_dashboard")
        except Exception as e:
            self.logger.error(f"DataDog API error in update_dashboard: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def delete_dashboard(self, dashboard_id: str, **kwargs: Any) -> DataDogResponse:
        """Delete a dashboard.

        DataDog SDK method: `delete_dashboard`

        Args:
            dashboard_id (required): Dashboard ID to delete

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: delete_dashboard")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["dashboard_id"] = dashboard_id
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.delete_dashboard(**kwargs_api)
            return self._handle_response(response, "delete_dashboard")
        except Exception as e:
            self.logger.error(f"DataDog API error in delete_dashboard: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    # ==================== Logs API ====================

    def list_logs(self, body: dict, **kwargs: Any) -> DataDogResponse:
        """List logs with search query.

        DataDog SDK method: `list_logs`

        Args:
            body (required): Log search request body

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: list_logs")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["body"] = body
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.list_logs(**kwargs_api)
            return self._handle_response(response, "list_logs")
        except Exception as e:
            self.logger.error(f"DataDog API error in list_logs: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def aggregate_logs(self, body: dict, **kwargs: Any) -> DataDogResponse:
        """Aggregate logs.

        DataDog SDK method: `aggregate_logs`

        Args:
            body (required): Log aggregation request body

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: aggregate_logs")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["body"] = body
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.aggregate_logs(**kwargs_api)
            return self._handle_response(response, "aggregate_logs")
        except Exception as e:
            self.logger.error(f"DataDog API error in aggregate_logs: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    # ==================== Metrics API ====================

    def query_metrics(self, query: str, from_ts: int, to_ts: int, **kwargs: Any) -> DataDogResponse:
        """Query metrics timeseries.

        DataDog SDK method: `query_metrics`

        Args:
            query (required): Metric query string
            from_ts (required): Start timestamp (epoch seconds)
            to_ts (required): End timestamp (epoch seconds)

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: query_metrics")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["query"] = query
            kwargs_api["from_ts"] = from_ts
            kwargs_api["to_ts"] = to_ts
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.query_metrics(**kwargs_api)
            return self._handle_response(response, "query_metrics")
        except Exception as e:
            self.logger.error(f"DataDog API error in query_metrics: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))


    def list_active_metrics(self, from_ts: int, host: str | None = None, tag_filter: str | None = None, **kwargs: Any) -> DataDogResponse:
        """List active metrics.

        DataDog SDK method: `list_active_metrics`

        Args:
            from_ts (required): Start timestamp (epoch seconds)
            host (optional): Hostname to filter
            tag_filter (optional): Tag filter

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error

        """
        self.logger.info("Calling DataDog API: list_active_metrics")
        try:
            kwargs_api: dict[str, Any] = {}
            kwargs_api["from_ts"] = from_ts
            if host is not None:
                kwargs_api["host"] = host
            if tag_filter is not None:
                kwargs_api["tag_filter"] = tag_filter
            if kwargs:
                kwargs_api.update(kwargs)
            response = self.client.list_active_metrics(**kwargs_api)
            return self._handle_response(response, "list_active_metrics")
        except Exception as e:
            self.logger.error(f"DataDog API error in list_active_metrics: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

__all__ = ["DataDogDataSource", "DataDogResponse"]
