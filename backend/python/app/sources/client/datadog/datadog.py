import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from datadog_api_client import ApiClient, Configuration # type: ignore
from datadog_api_client.v1.api import dashboards_api, metrics_api, monitors_api # type: ignore
from datadog_api_client.v2.api import logs_api # type: ignore
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class DataDogResponse:
    """Standardized DataDog API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class DataDogAPIConfig(BaseModel):
    """Configuration for DataDog SDK client

    Args:
        api_key: DataDog API key
        app_key: DataDog application key
        site: Optional DataDog site domain (default: ap1.datadoghq.com)
    """

    api_key: str
    app_key: str
    site: Optional[str] = "datadoghq.com"

    class Config:
        arbitrary_types_allowed = True

    def to_dict(self) -> dict:
        return self.model_dump()


class DataDogClient(IClient):
    """Wrapper over the official DataDog SDK client

    This class encapsulates the DataDog Python SDK (v1 and v2), exposing convenience,
    fallback, and integration points for metrics, monitors, logs, and dashboards.
    """

    def __init__(
        self,
        api_key: str,
        app_key: str,
        site: Optional[str] = "ap1.datadoghq.com",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize DataDog client with credentials

        Args:
            api_key: DataDog API key
            app_key: DataDog application key
            site: DataDog site domain (default: ap1.datadoghq.com)
            logger: Optional logger instance
        """
        self.api_key = api_key
        self.app_key = app_key
        self.site = site
        self.logger = logger or logging.getLogger(__name__)

        self.logger.info(f"Initializing DataDog client for site: {site}")

        # Build SDK configuration
        config = Configuration(
            api_key={"apiKeyAuth": api_key, "appKeyAuth": app_key},
            host=f"https://api.{site}",
        )
        self._client = ApiClient(config)

        # Initialize v1 APIs
        self._metrics_api = metrics_api.MetricsApi(self._client)
        self._monitors_api = monitors_api.MonitorsApi(self._client)
        self._dashboards_api = dashboards_api.DashboardsApi(self._client)

        # Initialize v2 APIs
        self._logs_api = logs_api.LogsApi(self._client)

        self.logger.info("DataDog client initialized successfully")

    def get_client(self) -> ApiClient:
        """Return the DataDog client object"""
        return self._client

    #  Metrics API 

    def query_metrics(self, query: str, from_ts: int, to_ts: int) -> DataDogResponse:
        """Query metrics (timeseries) from DataDog

        Args:
            query: metric query string, e.g. "system.cpu.idle{*}"
            from_ts: from timestamp (epoch seconds)
            to_ts: to timestamp (epoch seconds)

        Returns:
            DataDogResponse with metric query results
        """
        self.logger.info(f"Querying metrics: {query} from {from_ts} to {to_ts}")
        try:
            resp = self._metrics_api.query_metrics(_from=from_ts, to=to_ts, query=query)
            self.logger.info(f"Successfully queried metrics: {query}")
            return DataDogResponse(success=True, data=resp.to_dict(), message="OK")
        except Exception as e:
            self.logger.error(f"DataDog metric query failed: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    def list_active_metrics(self, from_ts: int, **kwargs) -> DataDogResponse:
        """List active metrics from DataDog

        Args:
            from_ts: from timestamp (epoch seconds)
            **kwargs: additional parameters (host, tag_filter, etc.)

        Returns:
            DataDogResponse with list of active metrics
        """
        self.logger.info(f"Listing active metrics from {from_ts}")
        try:
            resp = self._metrics_api.list_active_metrics(_from=from_ts, **kwargs)
            self.logger.info(f"Successfully retrieved {len(resp.get('metrics', []))} active metrics")
            return DataDogResponse(success=True, data=resp.to_dict(), message="OK")
        except Exception as e:
            self.logger.error(f"DataDog list_active_metrics failed: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    #  Monitors API 

    def list_monitors(self, **kwargs) -> DataDogResponse:
        """List monitors from DataDog

        Args:
            **kwargs: optional parameters (group_states, name, tags, etc.)

        Returns:
            DataDogResponse with list of monitors
        """
        self.logger.info("Listing monitors")
        try:
            resp = self._monitors_api.list_monitors(**kwargs)
            monitor_count = len(resp) if isinstance(resp, list) else 0
            self.logger.info(f"Successfully retrieved {monitor_count} monitors")
            return DataDogResponse(
                success=True,
                data={"monitors": [m.to_dict() for m in resp]} if isinstance(resp, list) else resp.to_dict(),
                message="OK",
            )
        except Exception as e:
            self.logger.error(f"DataDog list_monitors failed: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    def get_monitor(self, monitor_id: int, **kwargs) -> DataDogResponse:
        """Get a specific monitor by ID

        Args:
            monitor_id: Monitor ID
            **kwargs: optional parameters (group_states, with_downtimes, etc.)

        Returns:
            DataDogResponse with monitor details
        """
        self.logger.info(f"Getting monitor: {monitor_id}")
        try:
            resp = self._monitors_api.get_monitor(monitor_id=monitor_id, **kwargs)
            self.logger.info(f"Successfully retrieved monitor: {monitor_id}")
            return DataDogResponse(success=True, data=resp.to_dict(), message="OK")
        except Exception as e:
            self.logger.error(f"DataDog get_monitor failed for ID {monitor_id}: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    #  Dashboards API 

    def list_dashboards(self, **kwargs) -> DataDogResponse:
        """List all dashboards from DataDog

        Args:
            **kwargs: optional parameters (filter_shared, filter_deleted, etc.)

        Returns:
            DataDogResponse with list of dashboards
        """
        self.logger.info("Listing dashboards")
        try:
            resp = self._dashboards_api.list_dashboards(**kwargs)
            dashboard_count = len(resp.dashboards) if hasattr(resp, "dashboards") else 0
            self.logger.info(f"Successfully retrieved {dashboard_count} dashboards")
            return DataDogResponse(success=True, data=resp.to_dict(), message="OK")
        except Exception as e:
            self.logger.error(f"DataDog list_dashboards failed: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    def get_dashboard(self, dashboard_id: str) -> DataDogResponse:
        """Get a specific dashboard by ID

        Args:
            dashboard_id: Dashboard ID

        Returns:
            DataDogResponse with dashboard details
        """
        self.logger.info(f"Getting dashboard: {dashboard_id}")
        try:
            resp = self._dashboards_api.get_dashboard(dashboard_id=dashboard_id)
            self.logger.info(f"Successfully retrieved dashboard: {dashboard_id}")
            return DataDogResponse(success=True, data=resp.to_dict(), message="OK")
        except Exception as e:
            self.logger.error(f"DataDog get_dashboard failed for ID {dashboard_id}: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    def create_dashboard(self, body: dict) -> DataDogResponse:
        """Create a new dashboard

        Args:
            body: Dashboard definition (must follow DataDog dashboard schema)

        Returns:
            DataDogResponse with created dashboard details
        """
        self.logger.info("Creating new dashboard")
        try:
            resp = self._dashboards_api.create_dashboard(body=body)
            dashboard_id = resp.id if hasattr(resp, "id") else "unknown"
            self.logger.info(f"Successfully created dashboard: {dashboard_id}")
            return DataDogResponse(success=True, data=resp.to_dict(), message="Dashboard created")
        except Exception as e:
            self.logger.error(f"DataDog create_dashboard failed: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    def delete_dashboard(self, dashboard_id: str) -> DataDogResponse:
        """Delete a dashboard by ID

        Args:
            dashboard_id: Dashboard ID to delete

        Returns:
            DataDogResponse with deletion status
        """
        self.logger.info(f"Deleting dashboard: {dashboard_id}")
        try:
            resp = self._dashboards_api.delete_dashboard(dashboard_id=dashboard_id)
            self.logger.info(f"Successfully deleted dashboard: {dashboard_id}")
            return DataDogResponse(success=True, data=resp.to_dict(), message="Dashboard deleted")
        except Exception as e:
            self.logger.error(f"DataDog delete_dashboard failed for ID {dashboard_id}: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    #  Logs API

    def list_logs(self, body: dict) -> DataDogResponse:
        """List logs from DataDog using search query

        Args:
            body: Log search request body (must follow LogsListRequest schema)
                Example:
                {
                    "filter": {
                        "query": "*",
                        "from": "2024-01-01T00:00:00Z",
                        "to": "2024-01-01T01:00:00Z"
                    },
                    "page": {"limit": 100},
                    "sort": "timestamp"
                }

        Returns:
            DataDogResponse with log search results
        """
        self.logger.info(f"Listing logs with query: {body.get('filter', {}).get('query', 'N/A')}")
        try:
            resp = self._logs_api.list_logs(body=body)
            log_count = len(resp.data) if hasattr(resp, "data") and resp.data else 0
            self.logger.info(f"Successfully retrieved {log_count} logs")
            return DataDogResponse(success=True, data=resp.to_dict(), message="OK")
        except Exception as e:
            self.logger.error(f"DataDog list_logs failed: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    def aggregate_logs(self, body: dict) -> DataDogResponse:
        """Aggregate logs from DataDog

        Args:
            body: Log aggregation request body (must follow LogsAggregateRequest schema)

        Returns:
            DataDogResponse with log aggregation results
        """
        self.logger.info("Aggregating logs")
        try:
            resp = self._logs_api.aggregate_logs(body=body)
            self.logger.info("Successfully aggregated logs")
            return DataDogResponse(success=True, data=resp.to_dict(), message="OK")
        except Exception as e:
            self.logger.error(f"DataDog aggregate_logs failed: {e}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))

    #  Builder Methods 

    @classmethod
    def build_with_config(cls, config: DataDogAPIConfig, logger: Optional[logging.Logger] = None) -> "DataDogClient":
        """Build DataDogClient from DataDogAPIConfig

        Args:
            config: DataDogAPIConfig instance
            logger: Optional logger instance

        Returns:
            DataDogClient instance
        """
        return cls(api_key=config.api_key, app_key=config.app_key, site=config.site, logger=logger)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        arango_service: object,
        org_id: str,
        user_id: str,
    ) -> "DataDogClient":
        """Build DataDogClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            DataDogClient instance
        """
        # TODO: Implement service-based client construction
        # This would retrieve credentials from the configuration service
        raise NotImplementedError("Service-based client construction not yet implemented")

    def close(self) -> None:
        """Close the API client and cleanup resources"""
        self.logger.info("Closing DataDog client")
        if self._client:
            self._client.close()
            self.logger.info("DataDog client closed successfully")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # type: ignore
        """Context manager exit"""
        self.close()