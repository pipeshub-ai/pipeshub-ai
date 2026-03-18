#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Datadog (datadog-api-client) -- Code Generator (strict, typed)

Emits a `DatadogDataSource` with explicit, typed methods mapped to the
official datadog-api-client Python SDK.

SDK alignment:
- Dashboards: v1.DashboardsApi  (list_dashboards, get_dashboard, create_dashboard)
- Monitors:   v1.MonitorsApi    (list_monitors, get_monitor, create_monitor, update_monitor, delete_monitor)
- Users:      v2.UsersApi       (list_users, get_user)
- Hosts:      v1.HostsApi       (list_hosts)
- Metrics:    v1.MetricsApi     (query_timeseries)
- Metrics:    v2.MetricsApi     (list_active_metrics via list_tag_configurations)
- Incidents:  v2.IncidentsApi   (list_incidents, get_incident)
- Logs:       v2.LogsApi        (search_logs)
- Synthetics: v1.SyntheticsApi  (list_synthetics_tests, get_synthetics_test)
- Downtimes:  v1.DowntimesApi   (list_downtimes)
- Services:   v2.ServiceDefinitionApi (list_service_definitions)

References:
- https://github.com/DataDog/datadog-api-client-python
- https://datadoghq.dev/datadog-api-client-python/
"""

import argparse
import textwrap
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Configuration knobs (CLI-set)
# ---------------------------------------------------------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.datadog.datadog import DatadogResponse"
DEFAULT_CLASS_NAME = "DatadogDataSource"
DEFAULT_OUT = "app/sources/external/datadog/datadog.py"


HEADER = '''\
# ruff: noqa
from __future__ import annotations

from typing import Any, Dict, Optional, Union

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v1.api.dashboards_api import DashboardsApi
from datadog_api_client.v1.api.monitors_api import MonitorsApi
from datadog_api_client.v1.api.hosts_api import HostsApi
from datadog_api_client.v1.api.metrics_api import MetricsApi as MetricsApiV1
from datadog_api_client.v1.api.synthetics_api import SyntheticsApi
from datadog_api_client.v1.api.downtimes_api import DowntimesApi
from datadog_api_client.v1.model.dashboard import Dashboard
from datadog_api_client.v1.model.monitor import Monitor
from datadog_api_client.v1.model.monitor_update_request import MonitorUpdateRequest
from datadog_api_client.v2.api.users_api import UsersApi
from datadog_api_client.v2.api.incidents_api import IncidentsApi
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.api.metrics_api import MetricsApi as MetricsApiV2
from datadog_api_client.v2.api.service_definition_api import ServiceDefinitionApi
from datadog_api_client.v2.model.logs_list_request import LogsListRequest
from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage
from datadog_api_client.v2.model.logs_sort import LogsSort

{response_import}


class {class_name}:
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
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Dashboards (v1) ----------
METHODS += [
    (
        "list_dashboards(self) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = DashboardsApi(api_client)\n"
        "                result = api.list_dashboards()\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List all dashboards.  [dashboards]",
    ),
    (
        "get_dashboard(self, dashboard_id: str) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = DashboardsApi(api_client)\n"
        "                result = api.get_dashboard(dashboard_id=dashboard_id)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Get a single dashboard by ID.  [dashboards]",
    ),
    (
        "create_dashboard(self, body: Dict[str, Any]) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = DashboardsApi(api_client)\n"
        "                dashboard = Dashboard(**body)\n"
        "                result = api.create_dashboard(body=dashboard)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Create a dashboard. Pass the dashboard definition as a dict.  [dashboards]",
    ),
]

# ---------- Monitors (v1) ----------
METHODS += [
    (
        "list_monitors(self, group_states: Optional[str] = None, name: Optional[str] = None, tags: Optional[str] = None, monitor_tags: Optional[str] = None, page: Optional[int] = None, page_size: Optional[int] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = MonitorsApi(api_client)\n"
        "                kwargs = self._params(group_states=group_states, name=name, tags=tags, monitor_tags=monitor_tags, page=page, page_size=page_size)\n"
        "                result = api.list_monitors(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List monitors with optional filters.  [monitors]",
    ),
    (
        "get_monitor(self, monitor_id: int) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = MonitorsApi(api_client)\n"
        "                result = api.get_monitor(monitor_id=monitor_id)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Get a single monitor by ID.  [monitors]",
    ),
    (
        "create_monitor(self, body: Dict[str, Any]) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = MonitorsApi(api_client)\n"
        "                monitor = Monitor(**body)\n"
        "                result = api.create_monitor(body=monitor)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Create a monitor. Pass the monitor definition as a dict.  [monitors]",
    ),
    (
        "update_monitor(self, monitor_id: int, body: Dict[str, Any]) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = MonitorsApi(api_client)\n"
        "                update_req = MonitorUpdateRequest(**body)\n"
        "                result = api.update_monitor(monitor_id=monitor_id, body=update_req)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Update an existing monitor.  [monitors]",
    ),
    (
        "delete_monitor(self, monitor_id: int) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = MonitorsApi(api_client)\n"
        "                result = api.delete_monitor(monitor_id=monitor_id)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Delete a monitor by ID.  [monitors]",
    ),
]

# ---------- Users (v2) ----------
METHODS += [
    (
        "list_users(self, page_size: Optional[int] = None, page_number: Optional[int] = None, sort: Optional[str] = None, sort_dir: Optional[str] = None, filter_str: Optional[str] = None, filter_status: Optional[str] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = UsersApi(api_client)\n"
        "                kwargs: Dict[str, Any] = {}\n"
        "                if page_size is not None:\n"
        "                    kwargs['page_size'] = page_size\n"
        "                if page_number is not None:\n"
        "                    kwargs['page_number'] = page_number\n"
        "                if sort is not None:\n"
        "                    kwargs['sort'] = sort\n"
        "                if sort_dir is not None:\n"
        "                    kwargs['sort_dir'] = sort_dir\n"
        "                if filter_str is not None:\n"
        "                    kwargs['filter'] = filter_str\n"
        "                if filter_status is not None:\n"
        "                    kwargs['filter_status'] = filter_status\n"
        "                result = api.list_users(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List all users in the organization.  [users]",
    ),
    (
        "get_user(self, user_id: str) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = UsersApi(api_client)\n"
        "                result = api.get_user(user_id=user_id)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Get a single user by ID.  [users]",
    ),
]

# ---------- Hosts (v1) ----------
METHODS += [
    (
        "list_hosts(self, filter_str: Optional[str] = None, sort_field: Optional[str] = None, sort_dir: Optional[str] = None, start: Optional[int] = None, count: Optional[int] = None, from_ts: Optional[int] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = HostsApi(api_client)\n"
        "                kwargs: Dict[str, Any] = {}\n"
        "                if filter_str is not None:\n"
        "                    kwargs['filter'] = filter_str\n"
        "                if sort_field is not None:\n"
        "                    kwargs['sort_field'] = sort_field\n"
        "                if sort_dir is not None:\n"
        "                    kwargs['sort_dir'] = sort_dir\n"
        "                if start is not None:\n"
        "                    kwargs['start'] = start\n"
        "                if count is not None:\n"
        "                    kwargs['count'] = count\n"
        "                if from_ts is not None:\n"
        "                    kwargs['_from'] = from_ts\n"
        "                result = api.list_hosts(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List all hosts for the organization.  [hosts]",
    ),
]

# ---------- Metrics / Timeseries (v1) ----------
METHODS += [
    (
        "query_timeseries(self, from_ts: int, to_ts: int, query: str) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = MetricsApiV1(api_client)\n"
        "                result = api.query_metrics(_from=from_ts, to=to_ts, query=query)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Query timeseries data using a metrics query string.  [metrics]",
    ),
]

# ---------- Metrics (v2) - list_active_metrics via list_tag_configurations ----------
METHODS += [
    (
        "list_active_metrics(self, filter_configured: Optional[bool] = None, filter_tags_configured: Optional[str] = None, filter_metric_type: Optional[str] = None, filter_include_percentiles: Optional[bool] = None, filter_queried: Optional[bool] = None, filter_tags: Optional[str] = None, window_seconds: Optional[int] = None, page_size: Optional[int] = None, page_cursor: Optional[str] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = MetricsApiV2(api_client)\n"
        "                kwargs = self._params(\n"
        "                    filter_configured=filter_configured,\n"
        "                    filter_tags_configured=filter_tags_configured,\n"
        "                    filter_metric_type=filter_metric_type,\n"
        "                    filter_include_percentiles=filter_include_percentiles,\n"
        "                    filter_queried=filter_queried,\n"
        "                    filter_tags=filter_tags,\n"
        "                    window_seconds=window_seconds,\n"
        "                    page_size=page_size,\n"
        "                    page_cursor=page_cursor,\n"
        "                )\n"
        "                result = api.list_tag_configurations(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List active metric tag configurations with optional filters.  [metrics]",
    ),
]

# ---------- Incidents (v2) ----------
METHODS += [
    (
        "list_incidents(self, page_size: Optional[int] = None, page_offset: Optional[int] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = IncidentsApi(api_client)\n"
        "                kwargs = self._params(page_size=page_size, page_offset=page_offset)\n"
        "                result = api.list_incidents(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List all incidents.  [incidents]",
    ),
    (
        "get_incident(self, incident_id: str) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = IncidentsApi(api_client)\n"
        "                result = api.get_incident(incident_id=incident_id)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Get a single incident by ID.  [incidents]",
    ),
]

# ---------- Logs (v2) ----------
METHODS += [
    (
        "search_logs(self, filter_query: Optional[str] = None, filter_from: Optional[str] = None, filter_to: Optional[str] = None, sort: Optional[str] = None, page_cursor: Optional[str] = None, page_limit: Optional[int] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = LogsApi(api_client)\n"
        "                filter_obj = LogsQueryFilter()\n"
        "                if filter_query is not None:\n"
        "                    filter_obj.query = filter_query\n"
        "                if filter_from is not None:\n"
        "                    filter_obj._from = filter_from\n"
        "                if filter_to is not None:\n"
        "                    filter_obj.to = filter_to\n"
        "                body_kwargs: Dict[str, Any] = {'filter': filter_obj}\n"
        "                if sort is not None:\n"
        "                    body_kwargs['sort'] = LogsSort(sort)\n"
        "                if page_cursor is not None or page_limit is not None:\n"
        "                    page_obj = LogsListRequestPage()\n"
        "                    if page_cursor is not None:\n"
        "                        page_obj.cursor = page_cursor\n"
        "                    if page_limit is not None:\n"
        "                        page_obj.limit = page_limit\n"
        "                    body_kwargs['page'] = page_obj\n"
        "                request_body = LogsListRequest(**body_kwargs)\n"
        "                result = api.list_logs(body=request_body)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Search and filter logs.  [logs]",
    ),
]

# ---------- Synthetics (v1) ----------
METHODS += [
    (
        "list_synthetics_tests(self, page_size: Optional[int] = None, page_number: Optional[int] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = SyntheticsApi(api_client)\n"
        "                kwargs = self._params(page_size=page_size, page_number=page_number)\n"
        "                result = api.list_tests(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List all Synthetics tests.  [synthetics]",
    ),
    (
        "get_synthetics_test(self, public_id: str) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = SyntheticsApi(api_client)\n"
        "                result = api.get_test(public_id=public_id)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "Get a single Synthetics test by public ID.  [synthetics]",
    ),
]

# ---------- Downtimes (v1) ----------
METHODS += [
    (
        "list_downtimes(self, current_only: Optional[bool] = None, with_creator: Optional[bool] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = DowntimesApi(api_client)\n"
        "                kwargs = self._params(current_only=current_only, with_creator=with_creator)\n"
        "                result = api.list_downtimes(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List all scheduled downtimes.  [downtimes]",
    ),
]

# ---------- Service Definitions (v2) ----------
METHODS += [
    (
        "list_service_definitions(self, page_size: Optional[int] = None, page_number: Optional[int] = None, schema_version: Optional[str] = None) -> DatadogResponse",
        "            with ApiClient(self._config) as api_client:\n"
        "                api = ServiceDefinitionApi(api_client)\n"
        "                kwargs = self._params(page_size=page_size, page_number=page_number, schema_version=schema_version)\n"
        "                result = api.list_service_definitions(**kwargs)\n"
        "                return DatadogResponse(success=True, data=self._to_dict_safe(result))",
        "List all service definitions.  [service definitions]",
    ),
]


# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    # Dedent body then re-indent to sit inside try: (12 spaces = method + try)
    normalized_body = textwrap.indent(textwrap.dedent(body), "            ")
    return (
        f'    def {sig}:\n'
        f'        """{doc}"""\n'
        f'        try:\n'
        f'{normalized_body}\n'
        f'        except Exception as e:\n'
        f'            return DatadogResponse(success=False, error=str(e))\n'
    )


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT,
    class_name: str = DEFAULT_CLASS_NAME,
) -> str:
    parts: List[str] = []
    header = HEADER.replace("{response_import}", response_import).replace(
        "{class_name}", class_name
    )
    parts.append(header)
    for sig, body, doc in METHODS:
        parts.append(_emit_method(sig, body, doc))
    parts.append(FOOTER)
    return "".join(parts)


def write_output(path: str, code: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate DatadogDataSource (datadog-api-client SDK)."
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output path for the generated data source.",
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in DatadogResponse.",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS_NAME,
        help="Name of the generated datasource class.",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Also print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class(response_import=args.response_import, class_name=args.class_name)
    write_output(args.out, code)
    print(f"Generated {args.class_name} with {len(METHODS)} methods -> {args.out}")
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
