#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Smartsheet (smartsheet-python-sdk) -- Code Generator (strict, no `Any`, no `None` passthrough)

Emits a `SmartsheetDataSource` with explicit, typed methods mapped to *real* smartsheet-python-sdk APIs.
- No `Any` in signatures or implementation.
- Never forwards None to the SDK (filters optionals).
- Accepts either a raw `smartsheet.Smartsheet` instance or any client exposing `.get_sdk() -> smartsheet.Smartsheet`.

Doc alignment (examples):
- Sheets: ss.Sheets.list_sheets/get_sheet/create_sheet/update_sheet/delete_sheet.    [sheets]
- Rows: ss.Sheets.add_rows/update_rows/delete_rows.                                  [rows]
- Columns: ss.Sheets.get_columns/get_column/add_columns/update_column.               [columns]
- Workspaces: ss.Workspaces.list_workspaces/get_workspace.                            [workspaces]
- Folders: ss.Folders.list_folders/get_folder.                                        [folders]
- Reports: ss.Reports.list_reports/get_report.                                        [reports]
- Users: ss.Users.get_current_user/list_users.                                        [users]
- Search: ss.Search.search.                                                           [search]
- Home: ss.Home.list_all_contents.                                                    [home]
- Discussions: ss.Discussions.get_all_discussions.                                     [discussions]
- Attachments: ss.Attachments.list_all_attachments.                                   [attachments]

References (for maintainers):
- SDK: https://github.com/smartsheet/smartsheet-python-sdk
- API: https://smartsheet.redoc.ly/
"""

import argparse
import textwrap
from pathlib import Path
from typing import List, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.smartsheet.smartsheet import SmartsheetResponse"
DEFAULT_CLASS_NAME = "SmartsheetDataSource"
DEFAULT_OUT = "app/sources/external/smartsheet/smartsheet.py"


HEADER = '''\
# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportCallIssue=false
from __future__ import annotations

import smartsheet  # type: ignore[reportMissingTypeStubs]
from typing import Union, cast

{response_import}

class {class_name}:
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
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Users ----------
METHODS += [
    (
        "get_current_user(self) -> SmartsheetResponse",
        "            result = self._sdk.Users.get_current_user()\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Get the current authenticated user.  [users]",
    ),
    (
        "list_users(self, *, include_all: bool = True) -> SmartsheetResponse",
        "            result = self._sdk.Users.list_users(include_all=include_all)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all users in the organization.  [users]",
    ),
]

# ---------- Sheets ----------
METHODS += [
    (
        "list_sheets(self, *, page_size: int = 100, page: int = 1, include_all: bool = False, modified_since: Union[str, None] = None) -> SmartsheetResponse",
        "            params = self._params(page_size=page_size, page=page, include_all=include_all, modified_since=modified_since)\n"
        "            result = self._sdk.Sheets.list_sheets(**params)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all sheets the user has access to.  [sheets]",
    ),
    (
        "get_sheet(self, sheet_id: int, *, page_size: int = 100, page: int = 1) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.get_sheet(sheet_id, page_size=page_size, page=page)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Get a specific sheet by ID.  [sheets]",
    ),
    (
        "create_sheet(self, sheet_obj: object) -> SmartsheetResponse",
        "            result = self._sdk.Home.create_sheet(sheet_obj)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Create a new sheet at Home level. Pass a smartsheet.models.Sheet object.  [sheets]",
    ),
    (
        "create_sheet_in_folder(self, folder_id: int, sheet_obj: object) -> SmartsheetResponse",
        "            result = self._sdk.Folders.create_sheet_in_folder(folder_id, sheet_obj)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Create a new sheet in a specific folder.  [sheets]",
    ),
    (
        "create_sheet_in_workspace(self, workspace_id: int, sheet_obj: object) -> SmartsheetResponse",
        "            result = self._sdk.Workspaces.create_sheet_in_workspace(workspace_id, sheet_obj)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Create a new sheet in a specific workspace.  [sheets]",
    ),
    (
        "update_sheet(self, sheet_id: int, sheet_obj: object) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.update_sheet(sheet_id, sheet_obj)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Update a sheet (e.g. rename). Pass a smartsheet.models.Sheet object.  [sheets]",
    ),
    (
        "delete_sheet(self, sheet_id: int) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.delete_sheet(sheet_id)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Delete a sheet by ID.  [sheets]",
    ),
]

# ---------- Rows ----------
METHODS += [
    (
        "add_rows(self, sheet_id: int, row_objects: list[object]) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.add_rows(sheet_id, row_objects)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Add rows to a sheet. Pass a list of smartsheet.models.Row objects.  [rows]",
    ),
    (
        "update_rows(self, sheet_id: int, row_objects: list[object]) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.update_rows(sheet_id, row_objects)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Update rows in a sheet. Pass a list of smartsheet.models.Row objects.  [rows]",
    ),
    (
        "delete_rows(self, sheet_id: int, row_ids: list[int]) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.delete_rows(sheet_id, row_ids)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Delete rows from a sheet by row IDs.  [rows]",
    ),
]

# ---------- Columns ----------
METHODS += [
    (
        "list_columns(self, sheet_id: int, *, include_all: bool = True) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.get_columns(sheet_id, include_all=include_all)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all columns in a sheet.  [columns]",
    ),
    (
        "get_column(self, sheet_id: int, column_id: int) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.get_column(sheet_id, column_id)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Get a specific column in a sheet.  [columns]",
    ),
    (
        "add_columns(self, sheet_id: int, column_objects: list[object]) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.add_columns(sheet_id, column_objects)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Add columns to a sheet. Pass a list of smartsheet.models.Column objects.  [columns]",
    ),
    (
        "update_column(self, sheet_id: int, column_id: int, column_obj: object) -> SmartsheetResponse",
        "            result = self._sdk.Sheets.update_column(sheet_id, column_id, column_obj)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Update a column in a sheet. Pass a smartsheet.models.Column object.  [columns]",
    ),
]

# ---------- Workspaces ----------
METHODS += [
    (
        "list_workspaces(self) -> SmartsheetResponse",
        "            result = self._sdk.Workspaces.list_workspaces()\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all workspaces.  [workspaces]",
    ),
    (
        "get_workspace(self, workspace_id: int) -> SmartsheetResponse",
        "            result = self._sdk.Workspaces.get_workspace(workspace_id)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Get a specific workspace by ID.  [workspaces]",
    ),
]

# ---------- Folders ----------
METHODS += [
    (
        "list_folders(self, *, include_all: bool = True) -> SmartsheetResponse",
        "            result = self._sdk.Home.list_folders(include_all=include_all)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all top-level folders in the user's Home.  [folders]",
    ),
    (
        "get_folder(self, folder_id: int) -> SmartsheetResponse",
        "            result = self._sdk.Folders.get_folder(folder_id)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Get a specific folder by ID.  [folders]",
    ),
    (
        "list_workspace_folders(self, workspace_id: int) -> SmartsheetResponse",
        "            result = self._sdk.Workspaces.list_folders(workspace_id)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all folders in a workspace.  [folders]",
    ),
]

# ---------- Reports ----------
METHODS += [
    (
        "list_reports(self, *, page_size: int = 100, page: int = 1, modified_since: Union[str, None] = None) -> SmartsheetResponse",
        "            params = self._params(page_size=page_size, page=page, modified_since=modified_since)\n"
        "            result = self._sdk.Reports.list_reports(**params)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all reports the user has access to.  [reports]",
    ),
    (
        "get_report(self, report_id: int, *, page_size: int = 100, page: int = 1) -> SmartsheetResponse",
        "            result = self._sdk.Reports.get_report(report_id, page_size=page_size, page=page)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Get a specific report by ID.  [reports]",
    ),
]

# ---------- Search ----------
METHODS += [
    (
        "search(self, query: str) -> SmartsheetResponse",
        "            result = self._sdk.Search.search(query)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Search for sheets, reports, rows, etc.  [search]",
    ),
    (
        "search_sheet(self, sheet_id: int, query: str) -> SmartsheetResponse",
        "            result = self._sdk.Search.search_sheet(sheet_id, query)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Search within a specific sheet.  [search]",
    ),
]

# ---------- Home ----------
METHODS += [
    (
        "get_home(self) -> SmartsheetResponse",
        "            result = self._sdk.Home.list_all_contents()\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "Get the user's Home (top-level sheets, folders, workspaces, etc.).  [home]",
    ),
]

# ---------- Discussions ----------
METHODS += [
    (
        "list_sheet_discussions(self, sheet_id: int, *, include_all: bool = True) -> SmartsheetResponse",
        "            result = self._sdk.Discussions.get_all_discussions(sheet_id, include_all=include_all)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all discussions on a sheet.  [discussions]",
    ),
]

# ---------- Attachments ----------
METHODS += [
    (
        "list_sheet_attachments(self, sheet_id: int, *, include_all: bool = True) -> SmartsheetResponse",
        "            result = self._sdk.Attachments.list_all_attachments(sheet_id, include_all=include_all)\n"
        "            return SmartsheetResponse(success=True, data=result)",
        "List all attachments on a sheet.  [attachments]",
    ),
]


# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "        ")
    return f'    def {sig}:\n        """{doc}"""\n{normalized_body}\n'


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT, class_name: str = DEFAULT_CLASS_NAME
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
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SmartsheetDataSource (smartsheet-python-sdk)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in SmartsheetResponse.",
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
    print(f"Generated SmartsheetDataSource with {len(METHODS)} methods -> {args.out}")
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
