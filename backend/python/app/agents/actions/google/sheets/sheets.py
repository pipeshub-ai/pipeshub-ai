import asyncio
import json
import logging
from typing import List, Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.sheets.sheets import GoogleSheetsDataSource

logger = logging.getLogger(__name__)

class GoogleSheets:
    """Google Sheets tool exposed to the agents using GoogleSheetsDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Sheets tool"""
        """
        Args:
            client: Google Sheets client
        Returns:
            None
        """
        self.client = GoogleSheetsDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            asyncio.get_running_loop()
            # We're in an async context, use asyncio.run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run
            return asyncio.run(coro)

    @tool(
        app_name="sheets",
        tool_name="create_spreadsheet",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Title of the spreadsheet",
                required=False
            )
        ]
    )
    def create_spreadsheet(self, title: Optional[str] = None) -> tuple[bool, str]:
        """Create a new Google Sheets spreadsheet"""
        """
        Args:
            title: Title of the spreadsheet
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare spreadsheet data
            spreadsheet_data = {}
            if title:
                spreadsheet_data = {
                    "properties": {
                        "title": title
                    }
                }

            # Use GoogleSheetsDataSource method
            spreadsheet = self._run_async(self.client.spreadsheets_create(
                body=spreadsheet_data
            ))

            return True, json.dumps({
                "spreadsheet_id": spreadsheet.get("spreadsheetId", ""),
                "title": spreadsheet.get("properties", {}).get("title", ""),
                "url": spreadsheet.get("spreadsheetUrl", ""),
                "sheets": spreadsheet.get("sheets", []),
                "message": "Spreadsheet created successfully"
            })
        except Exception as e:
            logger.error(f"Failed to create spreadsheet: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sheets",
        tool_name="get_spreadsheet",
        parameters=[
            ToolParameter(
                name="spreadsheet_id",
                type=ParameterType.STRING,
                description="The ID of the spreadsheet to retrieve",
                required=True
            ),
            ToolParameter(
                name="ranges",
                type=ParameterType.STRING,
                description="Comma-separated list of ranges to retrieve",
                required=False
            ),
            ToolParameter(
                name="include_grid_data",
                type=ParameterType.BOOLEAN,
                description="Whether to include grid data",
                required=False
            )
        ]
    )
    def get_spreadsheet(
        self,
        spreadsheet_id: str,
        ranges: Optional[str] = None,
        include_grid_data: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get a Google Sheets spreadsheet"""
        """
        Args:
            spreadsheet_id: The ID of the spreadsheet
            ranges: Comma-separated list of ranges
            include_grid_data: Whether to include grid data
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleSheetsDataSource method
            spreadsheet = self._run_async(self.client.spreadsheets_get(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
                includeGridData=include_grid_data
            ))

            return True, json.dumps(spreadsheet)
        except Exception as e:
            logger.error(f"Failed to get spreadsheet: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sheets",
        tool_name="get_values",
        parameters=[
            ToolParameter(
                name="spreadsheet_id",
                type=ParameterType.STRING,
                description="The ID of the spreadsheet",
                required=True
            ),
            ToolParameter(
                name="range",
                type=ParameterType.STRING,
                description="The range to retrieve (e.g., 'Sheet1!A1:C10')",
                required=True
            ),
            ToolParameter(
                name="major_dimension",
                type=ParameterType.STRING,
                description="Major dimension (ROWS or COLUMNS)",
                required=False
            ),
            ToolParameter(
                name="value_render_option",
                type=ParameterType.STRING,
                description="How values should be rendered (FORMATTED_VALUE, UNFORMATTED_VALUE, FORMULA)",
                required=False
            )
        ]
    )
    def get_values(
        self,
        spreadsheet_id: str,
        range: str,
        major_dimension: Optional[str] = None,
        value_render_option: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get values from a spreadsheet range"""
        """
        Args:
            spreadsheet_id: The ID of the spreadsheet
            range: The range to retrieve
            major_dimension: Major dimension for the data
            value_render_option: How to render values
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleSheetsDataSource method
            values = self._run_async(self.client.spreadsheets_values_get(
                spreadsheetId=spreadsheet_id,
                range=range,
                majorDimension=major_dimension,
                valueRenderOption=value_render_option
            ))

            return True, json.dumps(values)
        except Exception as e:
            logger.error(f"Failed to get values: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sheets",
        tool_name="update_values",
        parameters=[
            ToolParameter(
                name="spreadsheet_id",
                type=ParameterType.STRING,
                description="The ID of the spreadsheet",
                required=True
            ),
            ToolParameter(
                name="range",
                type=ParameterType.STRING,
                description="The range to update (e.g., 'Sheet1!A1:C10')",
                required=True
            ),
            ToolParameter(
                name="values",
                type=ParameterType.ARRAY,
                description="2D array of values to write",
                required=True,
                items={"type": "array", "items": {"type": "string"}}
            ),
            ToolParameter(
                name="value_input_option",
                type=ParameterType.STRING,
                description="How input data should be interpreted (RAW, USER_ENTERED)",
                required=False
            )
        ]
    )
    def update_values(
        self,
        spreadsheet_id: str,
        range: str,
        values: List[List[str]],
        value_input_option: Optional[str] = None
    ) -> tuple[bool, str]:
        """Update values in a spreadsheet range"""
        """
        Args:
            spreadsheet_id: The ID of the spreadsheet
            range: The range to update
            values: 2D array of values to write
            value_input_option: How to interpret input data
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare update data
            update_data = {
                "values": values
            }

            # Use GoogleSheetsDataSource method
            result = self._run_async(self.client.spreadsheets_values_update(
                spreadsheetId=spreadsheet_id,
                range=range,
                valueInputOption=value_input_option,
                body=update_data
            ))

            return True, json.dumps({
                "spreadsheet_id": spreadsheet_id,
                "updated_range": result.get("updatedRange", ""),
                "updated_rows": result.get("updatedRows", 0),
                "updated_columns": result.get("updatedColumns", 0),
                "updated_cells": result.get("updatedCells", 0),
                "message": "Values updated successfully"
            })
        except Exception as e:
            logger.error(f"Failed to update values: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sheets",
        tool_name="append_values",
        parameters=[
            ToolParameter(
                name="spreadsheet_id",
                type=ParameterType.STRING,
                description="The ID of the spreadsheet",
                required=True
            ),
            ToolParameter(
                name="range",
                type=ParameterType.STRING,
                description="The range to append to (e.g., 'Sheet1!A1:C10')",
                required=True
            ),
            ToolParameter(
                name="values",
                type=ParameterType.ARRAY,
                description="2D array of values to append",
                required=True,
                items={"type": "array", "items": {"type": "string"}}
            ),
            ToolParameter(
                name="value_input_option",
                type=ParameterType.STRING,
                description="How input data should be interpreted (RAW, USER_ENTERED)",
                required=False
            )
        ]
    )
    def append_values(
        self,
        spreadsheet_id: str,
        range: str,
        values: List[List[str]],
        value_input_option: Optional[str] = None
    ) -> tuple[bool, str]:
        """Append values to a spreadsheet range"""
        """
        Args:
            spreadsheet_id: The ID of the spreadsheet
            range: The range to append to
            values: 2D array of values to append
            value_input_option: How to interpret input data
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare append data
            append_data = {
                "values": values
            }

            # Use GoogleSheetsDataSource method
            result = self._run_async(self.client.spreadsheets_values_append(
                spreadsheetId=spreadsheet_id,
                range=range,
                valueInputOption=value_input_option,
                body=append_data
            ))

            return True, json.dumps({
                "spreadsheet_id": spreadsheet_id,
                "updated_range": result.get("updatedRange", ""),
                "updated_rows": result.get("updatedRows", 0),
                "updated_columns": result.get("updatedColumns", 0),
                "updated_cells": result.get("updatedCells", 0),
                "message": "Values appended successfully"
            })
        except Exception as e:
            logger.error(f"Failed to append values: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sheets",
        tool_name="clear_values",
        parameters=[
            ToolParameter(
                name="spreadsheet_id",
                type=ParameterType.STRING,
                description="The ID of the spreadsheet",
                required=True
            ),
            ToolParameter(
                name="range",
                type=ParameterType.STRING,
                description="The range to clear (e.g., 'Sheet1!A1:C10')",
                required=True
            )
        ]
    )
    def clear_values(self, spreadsheet_id: str, range: str) -> tuple[bool, str]:
        """Clear values from a spreadsheet range"""
        """
        Args:
            spreadsheet_id: The ID of the spreadsheet
            range: The range to clear
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleSheetsDataSource method
            result = self._run_async(self.client.spreadsheets_values_clear(
                spreadsheetId=spreadsheet_id,
                range=range
            ))

            return True, json.dumps({
                "spreadsheet_id": spreadsheet_id,
                "cleared_range": result.get("clearedRange", ""),
                "message": "Values cleared successfully"
            })
        except Exception as e:
            logger.error(f"Failed to clear values: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="sheets",
        tool_name="batch_get_values",
        parameters=[
            ToolParameter(
                name="spreadsheet_id",
                type=ParameterType.STRING,
                description="The ID of the spreadsheet",
                required=True
            ),
            ToolParameter(
                name="ranges",
                type=ParameterType.ARRAY,
                description="List of ranges to retrieve",
                required=True,
                items={"type": "string"}
            ),
            ToolParameter(
                name="major_dimension",
                type=ParameterType.STRING,
                description="Major dimension (ROWS or COLUMNS)",
                required=False
            )
        ]
    )
    def batch_get_values(
        self,
        spreadsheet_id: str,
        ranges: List[str],
        major_dimension: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get values from multiple ranges in a spreadsheet"""
        """
        Args:
            spreadsheet_id: The ID of the spreadsheet
            ranges: List of ranges to retrieve
            major_dimension: Major dimension for the data
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleSheetsDataSource method
            values = self._run_async(self.client.spreadsheets_values_batch_get(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
                majorDimension=major_dimension
            ))

            return True, json.dumps(values)
        except Exception as e:
            logger.error(f"Failed to batch get values: {e}")
            return False, json.dumps({"error": str(e)})
