import json
import logging
from typing import Any, Optional

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.google_api_errors import (
    GoogleToolWording,
    google_error_message,
)
from app.agents.actions.util.tool_summaries import (
    args_template,
    confirmation,
    entity_summary,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import DocumentationLink
from app.connectors.sources.google.common.scopes import GOOGLE_TOOLSET_SCOPES
from app.sources.client.google.google import GoogleClient
from app.sources.client.google.serialized_execute import SerializedExecute

logger = logging.getLogger(__name__)

# Cells beyond this are cut from a read; ask for a smaller range to see the rest.
_MAX_CELLS_RETURNED = 20_000
_VALUE_INPUT_OPTIONS = ["USER_ENTERED", "RAW"]

_SHEETS_WORDING = GoogleToolWording(
    product="Google Sheets",
    toolset="Sheets",
    access="Google Sheets access",
    not_found=(
        "that spreadsheet or range. Check the spreadsheet id (the part of the link after /spreadsheets/d/) "
        "and the sheet name in the range, for example 'Sheet1!A1:C10'."
    ),
    gone="that spreadsheet has been deleted.",
)

_VALUES_ITEMS = {"type": "array", "items": {}}
_RANGE_HELP = "A range in A1 notation, for example 'Sheet1!A1:C10', or just a sheet name for the whole sheet"


class _SheetsInputError(ValueError):
    """Bad tool arguments; the message says what to change and is safe to show the agent."""


def _failure(error: Exception, action: str) -> tuple[bool, str]:
    logger.error("Failed to %s: %s", action, error)
    message = str(error) if isinstance(error, _SheetsInputError) else google_error_message(error, action, _SHEETS_WORDING)
    return False, json.dumps({"error": message})


def _spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def _rows(values: object) -> list[list[Any]]:
    """Rows to write: a list of rows, each a list of cells; a single flat row is accepted too."""
    if not isinstance(values, list) or not values:
        raise _SheetsInputError("Pass the cells as a list of rows, for example [[\"Name\", \"Total\"], [\"North\", 42]].")
    if not any(isinstance(row, list) for row in values):
        return [values]
    if not all(isinstance(row, list) for row in values):
        raise _SheetsInputError("Every row must be a list of cells, for example [[\"North\", 42], [\"South\", 17]].")
    return values


def _capped(value_ranges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whole rows of each range while one shared cell budget lasts; one entry per range, cut ones flagged."""
    room = _MAX_CELLS_RETURNED
    capped: list[dict[str, Any]] = []
    for value_range in value_ranges:
        rows = value_range.get("values", [])
        kept: list[list[Any]] = []
        for row in rows:
            if len(row) > room:
                break
            kept.append(row)
            room -= len(row)
        capped.append({"range": value_range.get("range", ""), "values": kept, "truncated": len(kept) < len(rows)})
    return capped


@ToolsetBuilder("Sheets")\
    .in_group("Google Workspace")\
    .with_description("Google Sheets integration for reading and updating spreadsheets")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Sheets",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="toolsets/oauth/callback/sheets",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=list(GOOGLE_TOOLSET_SCOPES["sheets"]),
            ),
            token_access_type="offline",
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            },
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path=IconPaths.connector_icon("sheets"),
            app_group="Google Workspace",
            app_description="Sheets OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("sheets"))
        .add_documentation_link(DocumentationLink(
            "Google Sheets API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/google-workspace/sheets",
            "pipeshub",
        )))\
    .build_decorator()
class GoogleSheets:
    """Google Sheets tools for agents, on a Sheets v4 client built from the toolset's OAuth sign-in."""

    def __init__(self, client: GoogleClient) -> None:
        self.client = client
        self._serial = SerializedExecute()

    async def _execute(self, request: Any) -> dict[str, Any]:  # noqa: ANN401
        return await self._serial.run(request.execute)

    def _values(self) -> Any:  # noqa: ANN401
        return self.client.spreadsheets().values()

    @tool(
        path="/tools/sheets/create_spreadsheet",
        short_description="Create a Google Sheet",
        description="Create a new Google spreadsheet in the connected account's Drive, optionally with named sheets (tabs).",
        parameters=[
            ToolParameter(name="title", type=ParameterType.STRING, description="The spreadsheet's title", required=True),
            ToolParameter(name="sheet_titles", type=ParameterType.ARRAY, items={"type": "string"}, description="Names of the sheets (tabs) to create; one 'Sheet1' if left out", required=False),
        ],
        tags=[Tag(key="category", value="spreadsheets"), Tag(key="type", value="write")],
        args_summary=args_template("Creating Google Sheet \"{title}\"", "title"),
        result_summary=entity_summary(lambda d: f"Created \"{d.get('title') or '?'}\"", path=()),
    )
    async def create_spreadsheet(self, title: str, sheet_titles: Optional[list[str]] = None) -> tuple[bool, str]:
        try:
            if not (title or "").strip():
                raise _SheetsInputError("Give the new spreadsheet a title.")
            body: dict[str, Any] = {"properties": {"title": title}}
            if sheet_titles:
                body["sheets"] = [{"properties": {"title": name}} for name in sheet_titles]
            created = await self._execute(self.client.spreadsheets().create(body=body))
            spreadsheet_id = created["spreadsheetId"]
            return True, json.dumps({
                "spreadsheet_id": spreadsheet_id,
                "title": (created.get("properties") or {}).get("title", title),
                "url": created.get("spreadsheetUrl") or _spreadsheet_url(spreadsheet_id),
                "sheets": [(s.get("properties") or {}).get("title", "") for s in created.get("sheets", [])],
            })
        except Exception as e:
            return _failure(e, "create the spreadsheet")

    @tool(
        path="/tools/sheets/get_spreadsheet",
        short_description="List a Google Sheet's tabs",
        description=(
            "Get a spreadsheet's title, link and its sheets (tabs) with their sizes, without the cell values. "
            "Use get_values to read cells."
        ),
        parameters=[
            ToolParameter(name="spreadsheet_id", type=ParameterType.STRING, description="The spreadsheet id (the part of the link after /spreadsheets/d/)", required=True),
        ],
        tags=[Tag(key="category", value="spreadsheets"), Tag(key="type", value="read")],
        args_summary=args_template("Opening Google Sheet {spreadsheet_id}", "spreadsheet_id"),
        result_summary=entity_summary(lambda d: f"\"{d.get('title') or '?'}\" has {len(d.get('sheets') or [])} sheet(s)", path=()),
    )
    async def get_spreadsheet(self, spreadsheet_id: str) -> tuple[bool, str]:
        try:
            found = await self._execute(self.client.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="spreadsheetId,spreadsheetUrl,properties.title,sheets.properties",
            ))
            sheets = []
            for sheet in found.get("sheets", []):
                props = sheet.get("properties") or {}
                grid = props.get("gridProperties") or {}
                sheets.append({
                    "title": props.get("title", ""),
                    "sheet_id": props.get("sheetId"),
                    "rows": grid.get("rowCount"),
                    "columns": grid.get("columnCount"),
                })
            return True, json.dumps({
                "spreadsheet_id": found.get("spreadsheetId", spreadsheet_id),
                "title": (found.get("properties") or {}).get("title", ""),
                "url": found.get("spreadsheetUrl") or _spreadsheet_url(spreadsheet_id),
                "sheets": sheets,
            })
        except Exception as e:
            return _failure(e, "open the spreadsheet")

    @tool(
        path="/tools/sheets/get_values",
        short_description="Read cells from a Google Sheet",
        description="Read the values in one range of a spreadsheet, as rows of cells.",
        parameters=[
            ToolParameter(name="spreadsheet_id", type=ParameterType.STRING, description="The spreadsheet id", required=True),
            ToolParameter(name="range", type=ParameterType.STRING, description=_RANGE_HELP, required=True),
        ],
        tags=[Tag(key="category", value="spreadsheets"), Tag(key="type", value="read")],
        args_summary=args_template("Reading {range} from Google Sheet {spreadsheet_id}", "range", "spreadsheet_id"),
        result_summary=entity_summary(lambda d: f"Read {len(d.get('values') or [])} row(s)", path=()),
    )
    async def get_values(self, spreadsheet_id: str, range: str) -> tuple[bool, str]:  # noqa: A002
        try:
            found = await self._execute(self._values().get(spreadsheetId=spreadsheet_id, range=range))
            return True, json.dumps(_capped([found])[0])
        except Exception as e:
            return _failure(e, "read the cells")

    @tool(
        path="/tools/sheets/batch_get_values",
        short_description="Read several ranges from a Google Sheet",
        description="Read the values in several ranges of one spreadsheet in a single call.",
        parameters=[
            ToolParameter(name="spreadsheet_id", type=ParameterType.STRING, description="The spreadsheet id", required=True),
            ToolParameter(name="ranges", type=ParameterType.ARRAY, items={"type": "string"}, description="Ranges in A1 notation, for example ['Sheet1!A1:B5', 'Totals']", required=True),
        ],
        tags=[Tag(key="category", value="spreadsheets"), Tag(key="type", value="read")],
        args_summary=args_template("Reading ranges from Google Sheet {spreadsheet_id}", "spreadsheet_id"),
        result_summary=entity_summary(lambda d: f"Read {len(d.get('ranges') or [])} range(s)", path=()),
    )
    async def batch_get_values(self, spreadsheet_id: str, ranges: list[str]) -> tuple[bool, str]:
        try:
            if not ranges:
                raise _SheetsInputError("Say which ranges to read, for example ['Sheet1!A1:B5'].")
            found = await self._execute(self._values().batchGet(spreadsheetId=spreadsheet_id, ranges=ranges))
            return True, json.dumps({"ranges": _capped(found.get("valueRanges", []))})
        except Exception as e:
            return _failure(e, "read the cells")

    @tool(
        path="/tools/sheets/update_values",
        short_description="Write cells in a Google Sheet",
        description=(
            "Overwrite the cells in a range with new values, starting at the range's top-left cell. "
            "With USER_ENTERED (the default) values are read as if typed into the sheet, so '=SUM(A1:A3)' "
            "becomes a formula and '2026-10-15' a date; RAW stores them exactly as given."
        ),
        parameters=[
            ToolParameter(name="spreadsheet_id", type=ParameterType.STRING, description="The spreadsheet id", required=True),
            ToolParameter(name="range", type=ParameterType.STRING, description=_RANGE_HELP, required=True),
            ToolParameter(name="values", type=ParameterType.ARRAY, items=_VALUES_ITEMS, description="Rows of cells, for example [[\"Name\", \"Total\"], [\"North\", 42]]", required=True),
            ToolParameter(name="value_input_option", type=ParameterType.STRING, enum=_VALUE_INPUT_OPTIONS, description="USER_ENTERED or RAW", required=False, default="USER_ENTERED"),
        ],
        tags=[Tag(key="category", value="spreadsheets"), Tag(key="type", value="write")],
        args_summary=args_template("Writing {range} in Google Sheet {spreadsheet_id}", "range", "spreadsheet_id"),
        result_summary=entity_summary(lambda d: f"Updated {d.get('updated_cells', 0)} cell(s)", path=()),
    )
    async def update_values(
        self, spreadsheet_id: str, range: str, values: list[list[Any]], value_input_option: str = "USER_ENTERED",  # noqa: A002
    ) -> tuple[bool, str]:
        try:
            rows = _rows(values)
            updated = await self._execute(self._values().update(
                spreadsheetId=spreadsheet_id, range=range, valueInputOption=value_input_option or "USER_ENTERED",
                body={"values": rows},
            ))
            return True, json.dumps({
                "updated_range": updated.get("updatedRange", range),
                "updated_rows": updated.get("updatedRows", 0),
                "updated_cells": updated.get("updatedCells", 0),
                "url": _spreadsheet_url(spreadsheet_id),
            })
        except Exception as e:
            return _failure(e, "write the cells")

    @tool(
        path="/tools/sheets/append_values",
        short_description="Add rows to a Google Sheet",
        description=(
            "Add rows after the last row of data in a range or sheet, without overwriting anything. "
            "Use this to log entries or add records to a table."
        ),
        parameters=[
            ToolParameter(name="spreadsheet_id", type=ParameterType.STRING, description="The spreadsheet id", required=True),
            ToolParameter(name="range", type=ParameterType.STRING, description="The table to add to, for example 'Sheet1!A:D' or just 'Sheet1'", required=True),
            ToolParameter(name="values", type=ParameterType.ARRAY, items=_VALUES_ITEMS, description="Rows to add, for example [[\"2026-10-15\", \"North\", 42]]", required=True),
            ToolParameter(name="value_input_option", type=ParameterType.STRING, enum=_VALUE_INPUT_OPTIONS, description="USER_ENTERED or RAW", required=False, default="USER_ENTERED"),
        ],
        tags=[Tag(key="category", value="spreadsheets"), Tag(key="type", value="write")],
        args_summary=args_template("Adding rows to {range} in Google Sheet {spreadsheet_id}", "range", "spreadsheet_id"),
        result_summary=entity_summary(lambda d: f"Added {d.get('appended_rows', 0)} row(s)", path=()),
    )
    async def append_values(
        self, spreadsheet_id: str, range: str, values: list[list[Any]], value_input_option: str = "USER_ENTERED",  # noqa: A002
    ) -> tuple[bool, str]:
        try:
            rows = _rows(values)
            appended = await self._execute(self._values().append(
                spreadsheetId=spreadsheet_id, range=range, valueInputOption=value_input_option or "USER_ENTERED",
                insertDataOption="INSERT_ROWS", body={"values": rows},
            ))
            updates = appended.get("updates") or {}
            return True, json.dumps({
                "updated_range": updates.get("updatedRange", ""),
                "appended_rows": updates.get("updatedRows", 0),
                "updated_cells": updates.get("updatedCells", 0),
                "url": _spreadsheet_url(spreadsheet_id),
            })
        except Exception as e:
            return _failure(e, "add the rows")

    @tool(
        path="/tools/sheets/clear_values",
        short_description="Clear cells in a Google Sheet",
        description="Erase the values in a range, keeping its formatting. This can't be undone from here, so confirm the range first.",
        parameters=[
            ToolParameter(name="spreadsheet_id", type=ParameterType.STRING, description="The spreadsheet id", required=True),
            ToolParameter(name="range", type=ParameterType.STRING, description=_RANGE_HELP, required=True),
        ],
        tags=[Tag(key="category", value="spreadsheets"), Tag(key="type", value="delete")],
        args_summary=args_template("Clearing {range} in Google Sheet {spreadsheet_id}", "range", "spreadsheet_id"),
        result_summary=confirmation("Cleared {range}", "range"),
    )
    async def clear_values(self, spreadsheet_id: str, range: str) -> tuple[bool, str]:  # noqa: A002
        try:
            if not (range or "").strip():
                raise _SheetsInputError("Say which range to clear, for example 'Sheet1!A2:D50'.")
            cleared = await self._execute(self._values().clear(spreadsheetId=spreadsheet_id, range=range, body={}))
            return True, json.dumps({"cleared_range": cleared.get("clearedRange", range), "url": _spreadsheet_url(spreadsheet_id)})
        except Exception as e:
            return _failure(e, "clear the cells")
