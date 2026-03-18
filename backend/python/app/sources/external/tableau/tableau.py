# ruff: noqa
from __future__ import annotations

import tableauserverclient as TSC  # type: ignore[reportMissingImports]
from typing import Any, Dict, List, Union, cast

from app.sources.client.tableau.tableau import TableauResponse


class TableauDataSource:
    """
    Typed wrapper over tableauserverclient for common Tableau business operations.

    Accepts either a TSC.Server instance or any client exposing `.get_sdk() -> TSC.Server`.

    SDK Reference: https://tableau.github.io/server-client-python/docs/
    """

    def __init__(self, client_or_sdk: Union[TSC.Server, object]) -> None:  # type: ignore[reportUnknownParameterType]
        super().__init__()
        if hasattr(client_or_sdk, "get_sdk"):  # type: ignore[reportUnknownArgumentType]
            sdk_obj = getattr(client_or_sdk, "get_sdk")()  # type: ignore[reportUnknownArgumentType]
            self._sdk: TSC.Server = cast(TSC.Server, sdk_obj)  # type: ignore[reportUnknownMemberType]
        else:
            self._sdk = cast(TSC.Server, client_or_sdk)  # type: ignore[reportUnknownMemberType]

    @staticmethod
    def _to_dict(item: object) -> Dict[str, Any]:
        """Convert a TSC resource item to a dictionary representation."""
        if hasattr(item, "__dict__"):
            return {k: v for k, v in item.__dict__.items() if not k.startswith("_")}
        return {"value": str(item)}

    @staticmethod
    def _to_dict_list(items: object) -> List[Dict[str, Any]]:
        """Convert a list of TSC resource items to a list of dictionaries."""
        result: List[Dict[str, Any]] = []
        if hasattr(items, "__iter__"):
            for item in items:  # type: ignore[union-attr, reportUnknownVariableType]
                if hasattr(item, "__dict__"):  # type: ignore[reportUnknownArgumentType]
                    result.append({k: v for k, v in item.__dict__.items() if not k.startswith("_")})  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
                else:
                    result.append({"value": str(item)})  # type: ignore[reportUnknownArgumentType]
        return result
    def list_workbooks(self) -> TableauResponse:
        """List all workbooks on the site.  [workbooks]"""
        items, pagination = self._sdk.workbooks.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def get_workbook(self, workbook_id: str) -> TableauResponse:
        """Get a single workbook by ID.  [workbooks]"""
        item: Any = self._sdk.workbooks.get_by_id(workbook_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict(item))  # type: ignore[reportUnknownArgumentType]
    def populate_workbook_views(self, workbook_id: str) -> TableauResponse:
        """Populate and return the views for a workbook.  [workbooks]"""
        workbook: Any = self._sdk.workbooks.get_by_id(workbook_id)  # type: ignore[reportUnknownMemberType]
        self._sdk.workbooks.populate_views(workbook)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict_list(workbook.views))  # type: ignore[reportUnknownArgumentType]
    def populate_workbook_connections(self, workbook_id: str) -> TableauResponse:
        """Populate and return the connections for a workbook.  [workbooks]"""
        workbook: Any = self._sdk.workbooks.get_by_id(workbook_id)  # type: ignore[reportUnknownMemberType]
        self._sdk.workbooks.populate_connections(workbook)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict_list(workbook.connections))  # type: ignore[reportUnknownArgumentType]
    def delete_workbook(self, workbook_id: str) -> TableauResponse:
        """Delete a workbook by ID.  [workbooks]"""
        self._sdk.workbooks.delete(workbook_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=True)  # type: ignore[reportUnknownArgumentType]
    def list_views(self) -> TableauResponse:
        """List all views on the site.  [views]"""
        items, pagination = self._sdk.views.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def get_view(self, view_id: str) -> TableauResponse:
        """Get a single view by ID.  [views]"""
        item: Any = self._sdk.views.get_by_id(view_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict(item))  # type: ignore[reportUnknownArgumentType]
    def list_datasources(self) -> TableauResponse:
        """List all published data sources on the site.  [datasources]"""
        items, pagination = self._sdk.datasources.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def get_datasource(self, datasource_id: str) -> TableauResponse:
        """Get a single data source by ID.  [datasources]"""
        item: Any = self._sdk.datasources.get_by_id(datasource_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict(item))  # type: ignore[reportUnknownArgumentType]
    def delete_datasource(self, datasource_id: str) -> TableauResponse:
        """Delete a data source by ID.  [datasources]"""
        self._sdk.datasources.delete(datasource_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=True)  # type: ignore[reportUnknownArgumentType]
    def list_projects(self) -> TableauResponse:
        """List all projects on the site.  [projects]"""
        items, pagination = self._sdk.projects.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def list_users(self) -> TableauResponse:
        """List all users on the site.  [users]"""
        items, pagination = self._sdk.users.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def get_user(self, user_id: str) -> TableauResponse:
        """Get a single user by ID.  [users]"""
        item: Any = self._sdk.users.get_by_id(user_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict(item))  # type: ignore[reportUnknownArgumentType]
    def list_groups(self) -> TableauResponse:
        """List all groups on the site.  [groups]"""
        items, pagination = self._sdk.groups.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def get_group(self, group_id: str) -> TableauResponse:
        """Get a single group by ID.  [groups]"""
        item: Any = self._sdk.groups.get_by_id(group_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict(item))  # type: ignore[reportUnknownArgumentType]
    def list_schedules(self) -> TableauResponse:
        """List all schedules on the server.  [schedules]"""
        items, pagination = self._sdk.schedules.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def list_jobs(self) -> TableauResponse:
        """List all jobs on the site.  [jobs]"""
        items, pagination = self._sdk.jobs.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def get_job(self, job_id: str) -> TableauResponse:
        """Get a single job by ID.  [jobs]"""
        item: Any = self._sdk.jobs.get_by_id(job_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict(item))  # type: ignore[reportUnknownArgumentType]
    def list_flows(self) -> TableauResponse:
        """List all flows on the site.  [flows]"""
        items, pagination = self._sdk.flows.get()  # type: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return TableauResponse(success=True, data=self._to_dict_list(items))  # type: ignore[reportUnknownArgumentType]
    def get_flow(self, flow_id: str) -> TableauResponse:
        """Get a single flow by ID.  [flows]"""
        item: Any = self._sdk.flows.get_by_id(flow_id)  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=self._to_dict(item))  # type: ignore[reportUnknownArgumentType]
    def sign_out(self) -> TableauResponse:
        """Sign out and invalidate the current auth session.  [auth]"""
        self._sdk.auth.sign_out()  # type: ignore[reportUnknownMemberType]
        return TableauResponse(success=True, data=True, message='Signed out successfully')  # type: ignore[reportUnknownArgumentType]
