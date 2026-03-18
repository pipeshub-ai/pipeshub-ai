# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportCallIssue=false
from __future__ import annotations

import smartsheet  # type: ignore[reportMissingTypeStubs]
from typing import Union, cast

from app.sources.client.smartsheet.smartsheet import SmartsheetResponse

class SmartsheetDataSource:
    """
    Strict, typed wrapper over smartsheet-python-sdk for common Smartsheet business operations.

    Accepts either a smartsheet `Smartsheet` instance *or* any object with `.get_sdk() -> smartsheet.Smartsheet`.
    """

    def __init__(self, client_or_sdk: Union[object, "smartsheet.Smartsheet"]) -> None:  # type: ignore[reportUnknownMemberType]
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: smartsheet.Smartsheet = cast("smartsheet.Smartsheet", sdk_obj)  # type: ignore[reportUnknownMemberType]
        else:
            self._sdk = cast("smartsheet.Smartsheet", client_or_sdk)  # type: ignore[reportUnknownMemberType]

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> dict[str, object]:
        # Filter out Nones to avoid overriding SDK defaults
        out: dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            # Skip empty containers that Smartsheet rejects in some endpoints
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
    def get_current_user(self) -> SmartsheetResponse:
        """Get the current authenticated user.  [users]"""
        result = self._sdk.Users.get_current_user()
        return SmartsheetResponse(success=True, data=result)
    def list_users(self, *, include_all: bool = True) -> SmartsheetResponse:
        """List all users in the organization.  [users]"""
        result = self._sdk.Users.list_users(include_all=include_all)
        return SmartsheetResponse(success=True, data=result)
    def list_sheets(self, *, page_size: int = 100, page: int = 1, include_all: bool = False, modified_since: Union[str, None] = None) -> SmartsheetResponse:
        """List all sheets the user has access to.  [sheets]"""
        params = self._params(page_size=page_size, page=page, include_all=include_all, modified_since=modified_since)
        result = self._sdk.Sheets.list_sheets(**params)
        return SmartsheetResponse(success=True, data=result)
    def get_sheet(self, sheet_id: int, *, page_size: int = 100, page: int = 1) -> SmartsheetResponse:
        """Get a specific sheet by ID.  [sheets]"""
        result = self._sdk.Sheets.get_sheet(sheet_id, page_size=page_size, page=page)
        return SmartsheetResponse(success=True, data=result)
    def create_sheet(self, sheet_obj: object) -> SmartsheetResponse:
        """Create a new sheet at Home level. Pass a smartsheet.models.Sheet object.  [sheets]"""
        result = self._sdk.Home.create_sheet(sheet_obj)
        return SmartsheetResponse(success=True, data=result)
    def create_sheet_in_folder(self, folder_id: int, sheet_obj: object) -> SmartsheetResponse:
        """Create a new sheet in a specific folder.  [sheets]"""
        result = self._sdk.Folders.create_sheet_in_folder(folder_id, sheet_obj)
        return SmartsheetResponse(success=True, data=result)
    def create_sheet_in_workspace(self, workspace_id: int, sheet_obj: object) -> SmartsheetResponse:
        """Create a new sheet in a specific workspace.  [sheets]"""
        result = self._sdk.Workspaces.create_sheet_in_workspace(workspace_id, sheet_obj)
        return SmartsheetResponse(success=True, data=result)
    def update_sheet(self, sheet_id: int, sheet_obj: object) -> SmartsheetResponse:
        """Update a sheet (e.g. rename). Pass a smartsheet.models.Sheet object.  [sheets]"""
        result = self._sdk.Sheets.update_sheet(sheet_id, sheet_obj)
        return SmartsheetResponse(success=True, data=result)
    def delete_sheet(self, sheet_id: int) -> SmartsheetResponse:
        """Delete a sheet by ID.  [sheets]"""
        result = self._sdk.Sheets.delete_sheet(sheet_id)
        return SmartsheetResponse(success=True, data=result)
    def add_rows(self, sheet_id: int, row_objects: list[object]) -> SmartsheetResponse:
        """Add rows to a sheet. Pass a list of smartsheet.models.Row objects.  [rows]"""
        result = self._sdk.Sheets.add_rows(sheet_id, row_objects)
        return SmartsheetResponse(success=True, data=result)
    def update_rows(self, sheet_id: int, row_objects: list[object]) -> SmartsheetResponse:
        """Update rows in a sheet. Pass a list of smartsheet.models.Row objects.  [rows]"""
        result = self._sdk.Sheets.update_rows(sheet_id, row_objects)
        return SmartsheetResponse(success=True, data=result)
    def delete_rows(self, sheet_id: int, row_ids: list[int]) -> SmartsheetResponse:
        """Delete rows from a sheet by row IDs.  [rows]"""
        result = self._sdk.Sheets.delete_rows(sheet_id, row_ids)
        return SmartsheetResponse(success=True, data=result)
    def list_columns(self, sheet_id: int, *, include_all: bool = True) -> SmartsheetResponse:
        """List all columns in a sheet.  [columns]"""
        result = self._sdk.Sheets.get_columns(sheet_id, include_all=include_all)
        return SmartsheetResponse(success=True, data=result)
    def get_column(self, sheet_id: int, column_id: int) -> SmartsheetResponse:
        """Get a specific column in a sheet.  [columns]"""
        result = self._sdk.Sheets.get_column(sheet_id, column_id)
        return SmartsheetResponse(success=True, data=result)
    def add_columns(self, sheet_id: int, column_objects: list[object]) -> SmartsheetResponse:
        """Add columns to a sheet. Pass a list of smartsheet.models.Column objects.  [columns]"""
        result = self._sdk.Sheets.add_columns(sheet_id, column_objects)
        return SmartsheetResponse(success=True, data=result)
    def update_column(self, sheet_id: int, column_id: int, column_obj: object) -> SmartsheetResponse:
        """Update a column in a sheet. Pass a smartsheet.models.Column object.  [columns]"""
        result = self._sdk.Sheets.update_column(sheet_id, column_id, column_obj)
        return SmartsheetResponse(success=True, data=result)
    def list_workspaces(self) -> SmartsheetResponse:
        """List all workspaces.  [workspaces]"""
        result = self._sdk.Workspaces.list_workspaces()
        return SmartsheetResponse(success=True, data=result)
    def get_workspace(self, workspace_id: int) -> SmartsheetResponse:
        """Get a specific workspace by ID.  [workspaces]"""
        result = self._sdk.Workspaces.get_workspace(workspace_id)
        return SmartsheetResponse(success=True, data=result)
    def list_folders(self, *, include_all: bool = True) -> SmartsheetResponse:
        """List all top-level folders in the user's Home.  [folders]"""
        result = self._sdk.Home.list_folders(include_all=include_all)
        return SmartsheetResponse(success=True, data=result)
    def get_folder(self, folder_id: int) -> SmartsheetResponse:
        """Get a specific folder by ID.  [folders]"""
        result = self._sdk.Folders.get_folder(folder_id)
        return SmartsheetResponse(success=True, data=result)
    def list_workspace_folders(self, workspace_id: int) -> SmartsheetResponse:
        """List all folders in a workspace.  [folders]"""
        result = self._sdk.Workspaces.list_folders(workspace_id)
        return SmartsheetResponse(success=True, data=result)
    def list_reports(self, *, page_size: int = 100, page: int = 1, modified_since: Union[str, None] = None) -> SmartsheetResponse:
        """List all reports the user has access to.  [reports]"""
        params = self._params(page_size=page_size, page=page, modified_since=modified_since)
        result = self._sdk.Reports.list_reports(**params)
        return SmartsheetResponse(success=True, data=result)
    def get_report(self, report_id: int, *, page_size: int = 100, page: int = 1) -> SmartsheetResponse:
        """Get a specific report by ID.  [reports]"""
        result = self._sdk.Reports.get_report(report_id, page_size=page_size, page=page)
        return SmartsheetResponse(success=True, data=result)
    def search(self, query: str) -> SmartsheetResponse:
        """Search for sheets, reports, rows, etc.  [search]"""
        result = self._sdk.Search.search(query)
        return SmartsheetResponse(success=True, data=result)
    def search_sheet(self, sheet_id: int, query: str) -> SmartsheetResponse:
        """Search within a specific sheet.  [search]"""
        result = self._sdk.Search.search_sheet(sheet_id, query)
        return SmartsheetResponse(success=True, data=result)
    def get_home(self) -> SmartsheetResponse:
        """Get the user's Home (top-level sheets, folders, workspaces, etc.).  [home]"""
        result = self._sdk.Home.list_all_contents()
        return SmartsheetResponse(success=True, data=result)
    def list_sheet_discussions(self, sheet_id: int, *, include_all: bool = True) -> SmartsheetResponse:
        """List all discussions on a sheet.  [discussions]"""
        result = self._sdk.Discussions.get_all_discussions(sheet_id, include_all=include_all)
        return SmartsheetResponse(success=True, data=result)
    def list_sheet_attachments(self, sheet_id: int, *, include_all: bool = True) -> SmartsheetResponse:
        """List all attachments on a sheet.  [attachments]"""
        result = self._sdk.Attachments.list_all_attachments(sheet_id, include_all=include_all)
        return SmartsheetResponse(success=True, data=result)

