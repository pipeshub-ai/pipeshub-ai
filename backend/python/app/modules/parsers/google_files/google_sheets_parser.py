"""Google Sheets Parser module for parsing Google Sheets content"""

import json
from typing import Any, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential

from app.connectors.sources.google.admin.google_admin_service import GoogleAdminService
from app.connectors.utils.decorators import exponential_backoff
from app.modules.parsers.excel.prompt_template import (
    prompt,
    row_text_prompt,
    table_summary_prompt,
)
from app.modules.parsers.google_files.parser_user_service import ParserUserService


class GoogleSheetsParser:
    """Parser class for Google Sheets content"""

    def __init__(
        self,
        logger,
        admin_service: Optional[GoogleAdminService] = None,
        user_service: Optional[ParserUserService] = None,
    ) -> None:
        """Initialize with either admin or user service"""
        self.logger = logger
        self.admin_service = admin_service
        self.user_service = user_service
        self.service = None

        self.table_summary_prompt = table_summary_prompt
        self.row_text_prompt = row_text_prompt

        # Configure retry parameters
        self.max_retries = 3
        self.min_wait = 1  # seconds
        self.max_wait = 10  # seconds

    async def connect_service(
        self, user_email: str = None, org_id: str = None, user_id: str = None, app_name: str = "drive"
    ) -> None:
        if self.user_service:
            if not await self.user_service.connect_individual_user(org_id, user_id,app_name=app_name):
                self.logger.error("❌ Failed to connect to Google Sheets service")
                return None

            self.service = self.user_service.sheets_service
            self.logger.info("🚀 Connected to Google Sheets service: %s", self.service)
        elif self.admin_service:
            user_service = await self.admin_service.create_parser_user_service(
                user_email
            )
            self.service = user_service.sheets_service
            self.logger.info("🚀 Connected to Google Sheets service: %s", self.service)

    async def list_spreadsheets(self) -> List[Dict]:
        """List all Google Sheets in the user's Drive"""
        try:
            # Get both file metadata and content in a single call
            results = (
                self.drive_service.files()
                .list(
                    q="mimeType='application/vnd.google-apps.spreadsheet'",
                    spaces="drive",
                    fields="files(id, name, createdTime, modifiedTime, properties, version)",
                    pageSize=100,
                )
                .execute()
            )

            spreadsheets = results.get("files", [])
            self.logger.info("✅ Found %d spreadsheets", len(spreadsheets))
            return spreadsheets

        except Exception as e:
            self.logger.error("❌ Failed to list spreadsheets: %s", str(e))
            return []

    @exponential_backoff()
    async def parse_spreadsheet(self, spreadsheet_id: str) -> Dict[str, Any]:
        """Parse Google Sheets file and extract content similar to Excel parser"""
        try:
            # Get spreadsheet metadata
            spreadsheet = (
                self.service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            )

            sheets_data = []
            total_rows = 0
            total_cells = 0
            all_text = []

            # Process each sheet
            for sheet in spreadsheet["sheets"]:
                sheet_props = sheet["properties"]
                sheet_name = sheet_props["title"]

                # Get sheet data
                range_name = f"{sheet_name}!A1:ZZ"
                result = (
                    self.service.spreadsheets()
                    .values()
                    .get(spreadsheetId=spreadsheet_id, range=range_name)
                    .execute()
                )
                values = result.get("values", [])

                if not values:
                    continue

                # Process headers and data
                headers = values[0] if values else []
                data = []

                for row_idx, row in enumerate(values[1:], 2):
                    row_data = []
                    # Pad row with None values if needed
                    padded_row = row + [None] * (len(headers) - len(row))

                    for col_idx, value in enumerate(padded_row, 1):
                        cell_data = {
                            "value": value,
                            "header": (
                                headers[col_idx - 1]
                                if col_idx - 1 < len(headers)
                                else None
                            ),
                            "row": row_idx,
                            "column": col_idx,
                            "column_letter": self._get_column_letter(col_idx),
                            "coordinate": f"{self._get_column_letter(col_idx)}{row_idx}",
                            "data_type": self._get_data_type(value),
                        }
                        row_data.append(cell_data)
                        if value:
                            total_cells += 1
                            all_text.append(str(value))

                    data.append(row_data)

                total_rows += len(values)
                sheets_data.append(
                    {
                        "name": sheet_name,
                        "data": data,
                        "headers": headers,
                        "row_count": sheet_props["gridProperties"]["rowCount"],
                        "column_count": sheet_props["gridProperties"]["columnCount"],
                    }
                )

            # Prepare metadata
            metadata = {
                "title": spreadsheet.get("properties", {}).get("title"),
                "locale": spreadsheet.get("properties", {}).get("locale"),
                "timeZone": spreadsheet.get("properties", {}).get("timeZone"),
                "sheet_count": len(spreadsheet["sheets"]),
            }

            return {
                "sheets": sheets_data,
                "metadata": metadata,
                "text_content": "\n".join(all_text),
                "sheet_names": [sheet["name"] for sheet in sheets_data],
                "total_rows": total_rows,
                "total_cells": total_cells,
            }

        except Exception as e:
            error_msg = str(e)
            if "SERVICE_DISABLED" in error_msg or "API has not been used" in error_msg:
                self.logger.error(f"❌ Google Sheets API is not enabled. Please enable it in Google Cloud Console: {error_msg}")
            elif "PERMISSION_DENIED" in error_msg:
                self.logger.error(f"❌ Permission denied for Google Sheets API: {error_msg}")
            else:
                self.logger.error(f"❌ Failed to parse spreadsheet: {error_msg}")
            raise

    def _get_column_letter(self, col_idx: int) -> str:
        """Convert column index to letter (1 = A, 27 = AA, etc.)"""
        result = ""
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _get_data_type(self, value) -> str:
        """Determine the data type of a value"""
        if value is None:
            return "n"  # null
        elif isinstance(value, bool):
            return "b"  # boolean
        elif isinstance(value, (int, float)):
            return "n"  # numeric
        elif isinstance(value, str):
            return "s"  # string
        return "s"  # default to string

    @exponential_backoff()
    async def find_tables(
        self, sheet_name: str, spreadsheet_id: str
    ) -> List[Dict[str, Any]]:
        """Find and process all tables in a sheet"""
        try:
            tables = []
            visited_cells = set()

            # Get sheet data
            range_name = f"{sheet_name}!A1:ZZ"
            result = (
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute()
            )
            values = result.get("values", [])

            if not values:
                return tables

            def get_table(start_row, start_col) -> Optional[Dict[str, Any]]:
                """Extract a table starting from (start_row, start_col)."""
                if start_row >= len(values):
                    return None

                # Find the last column of the table
                max_col = start_col
                for col in range(
                    start_col, len(values[start_row]) if start_row < len(values) else 0
                ):
                    has_data = False
                    for row in range(start_row, len(values)):
                        if row < len(values) and col < len(values[row]):
                            if values[row][col]:
                                has_data = True
                                max_col = col
                                break
                    if not has_data:
                        break

                # Find the last row of the table
                max_row = start_row
                for row in range(start_row, len(values)):
                    has_data = False
                    for col in range(start_col, max_col + 1):
                        if row < len(values) and col < len(values[row]):
                            if values[row][col]:
                                has_data = True
                                max_row = row
                                break
                    if not has_data:
                        break

                # Extract table data
                table_values = []
                for row in range(start_row, max_row + 1):
                    table_row = []
                    for col in range(start_col, max_col + 1):
                        value = (
                            values[row][col]
                            if row < len(values) and col < len(values[row])
                            else ""
                        )
                        table_row.append(value)
                    table_values.append(table_row)

                # Denormalize the extracted table
                denormalized_values = self.denormalize_sheet(table_values)

                # Process table data
                table_data = []
                headers = []

                # Process header row using denormalized values
                header_cells = []
                for col in range(len(denormalized_values[0])):
                    value = denormalized_values[0][col]
                    header_cell = self._process_cell(
                        value, None, start_row + 1, start_col + col + 1
                    )
                    header_cells.append(header_cell)
                    if value:
                        visited_cells.add((start_row, start_col + col))

                # Only consider it a header row if at least one cell has data
                if any(cell["value"] is not None for cell in header_cells):
                    headers = [cell["value"] for cell in header_cells]
                    table_data.append(header_cells)
                else:
                    return None

                # Process data rows using denormalized values
                for row_idx in range(1, len(denormalized_values)):
                    row_data = []
                    for col_idx in range(len(denormalized_values[row_idx])):
                        value = denormalized_values[row_idx][col_idx]
                        header = headers[col_idx] if col_idx < len(headers) else None
                        cell_data = self._process_cell(
                            value,
                            header,
                            start_row + row_idx + 1,
                            start_col + col_idx + 1,
                        )
                        row_data.append(cell_data)
                        if value:
                            visited_cells.add(
                                (start_row + row_idx, start_col + col_idx)
                            )
                    table_data.append(row_data)

                return {
                    "headers": headers,
                    "data": table_data[1:] if table_data else [],
                    "start_row": start_row + 1,
                    "start_col": start_col + 1,
                    "end_row": max_row + 1,
                    "end_col": max_col + 1,
                }

            # Find all tables in the sheet
            for row_idx, row in enumerate(values):
                for col_idx, cell in enumerate(row):
                    if (
                        cell
                        and isinstance(cell, str)
                        and (row_idx, col_idx) not in visited_cells
                    ):
                        table = get_table(row_idx, col_idx)
                        if table and table["data"]:  # Only add if table has data
                            tables.append(table)

            return tables

        except Exception as e:
            self.logger.error(f"❌ Error finding tables: {str(e)}")
            raise

    def denormalize_sheet(self, values: List[List[str]]) -> List[List[str]]:
        """Fill merged/empty cells by propagating values down and right."""
        if not values:
            return values

        max_cols = max(len(row) for row in values)

        # Normalize row lengths
        for row in values:
            while len(row) < max_cols:
                row.append("")

        # Fill right (across columns) - useful if headings span across columns
        for row in values:
            last_val = ""
            for col_idx in range(len(row)):
                if row[col_idx]:
                    last_val = row[col_idx]
                elif last_val:
                    row[col_idx] = last_val

        # Fill down (across rows) - useful for section headers like 'IG FEED'
        for col_idx in range(max_cols):
            last_val = ""
            for row_idx in range(len(values)):
                if values[row_idx][col_idx]:
                    last_val = values[row_idx][col_idx]
                elif last_val:
                    values[row_idx][col_idx] = last_val

        return values

    def _process_cell(
        self, value, header: str, row: int, col: int
    ) -> Dict[str, Any]:
        """Process a single cell and return its data"""
        return {
            "value": value,
            "header": header,
            "row": row,
            "column": col,
            "column_letter": self._get_column_letter(col),
            "coordinate": f"{self._get_column_letter(col)}{row}",
            "data_type": self._get_data_type(value),
            "style": {"font": {}, "fill": {}, "alignment": {}},
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=lambda retry_state: retry_state.args[0].logger.warning(
            f"Retrying LLM call after error. Attempt {retry_state.attempt_number}"
        ),
    )
    async def _call_llm(self, messages) -> dict:
        """Wrapper for LLM calls with retry logic"""
        return await self.llm.ainvoke(messages)

    @exponential_backoff()
    async def get_tables_in_sheet(
        self, sheet_name: str, spreadsheet_id: str
    ) -> List[Dict[str, Any]]:
        """Get all tables in a specific sheet"""
        try:
            tables = await self.find_tables(sheet_name, spreadsheet_id)
            tables_context = []
            for idx, table in enumerate(tables, 1):
                table_data = [[cell["value"] for cell in row] for row in table["data"]]
                tables_context.append(f"Table {idx}:\n{table_data}")

            # Process each table with LLM similar to Excel parser
            processed_tables = []
            for table in tables:
                table_data = [[cell["value"] for cell in row] for row in table["data"]]

                # Format prompt and get LLM response
                formatted_prompt = prompt.format(
                    table_data=table_data,
                    tables_context=tables_context,
                    start_row=table["start_row"],
                    start_col=table["start_col"],
                    end_row=table["end_row"],
                    end_col=table["end_col"],
                    num_columns=len(table["data"][0]) if table["data"] else 0,
                )

                # Get LLM response with retry
                messages = [
                    {
                        "role": "system",
                        "content": "You are a data analysis expert. Respond with only the list of headers.",
                    },
                    {"role": "user", "content": formatted_prompt},
                ]
                response = await self._call_llm(messages)
                if '</think>' in response.content:
                    response.content = response.content.split('</think>')[-1]
                try:
                    new_headers = [
                        h.strip() for h in response.content.strip().split(",")
                    ]
                    if len(new_headers) != len(table["data"][0]):
                        new_headers = table["headers"]

                    processed_tables.append(
                        {
                            "headers": new_headers,
                            "data": table["data"],
                            "start_row": table["start_row"],
                            "start_col": table["start_col"],
                            "end_row": table["end_row"],
                            "end_col": table["end_col"],
                        }
                    )

                except Exception:
                    processed_tables.append(table)

            return processed_tables

        except Exception:
            raise

    @exponential_backoff()
    async def process_sheet_with_summaries(
        self, llm, sheet_name: str, spreadsheet_id: str
    ) -> Dict[str, Any]:
        """Process a sheet and generate all summaries and row texts"""
        try:
            self.llm = llm
            # Get tables in the sheet
            tables = await self.get_tables_in_sheet(sheet_name, spreadsheet_id)
            # Process each table
            processed_tables = []
            for table in tables:
                # Get table summary
                table_summary = await self.get_table_summary(table)
                # Process rows in batches of 20
                processed_rows = []
                batch_size = 20
                for i in range(0, len(table["data"]), batch_size):
                    batch = table["data"][i : i + batch_size]
                    row_texts = await self.get_rows_text(batch, table_summary)
                    # Add processed rows to results
                    for row, row_text in zip(batch, row_texts):
                        processed_rows.append(
                            {
                                "raw_data": {
                                    cell["header"]: cell["value"] for cell in row
                                },
                                "natural_language_text": row_text,
                                "row_num": row[0]["row"],
                            }
                        )
                processed_tables.append(
                    {
                        "headers": table["headers"],
                        "summary": table_summary,
                        "rows": processed_rows,
                        "location": {
                            "start_row": table["start_row"],
                            "start_col": table["start_col"],
                            "end_row": table["end_row"],
                            "end_col": table["end_col"],
                        },
                    }
                )
            return {"sheet_name": sheet_name, "tables": processed_tables}

        except Exception as e:
            self.logger.error(f"❌ Error processing sheet with summaries: {str(e)}")
            raise

    @exponential_backoff()
    async def get_table_summary(self, table: Dict[str, Any]) -> str:
        """Get a natural language summary of a specific table"""
        try:
            # Prepare sample data
            sample_data = [
                {cell["header"]: cell["value"] for cell in row}
                for row in table["data"][:3]  # Use first 3 rows as sample
            ]

            # Get summary from LLM with retry
            messages = self.table_summary_prompt.format_messages(
                headers=table["headers"], sample_data=json.dumps(sample_data, indent=2)
            )
            response = await self._call_llm(messages)
            if '</think>' in response.content:
                response.content = response.content.split('</think>')[-1]
            return response.content

        except Exception as e:
            self.logger.error(f"❌ Error getting table summary: {str(e)}")
            raise

    @exponential_backoff()
    async def get_rows_text(
        self, rows: List[List[Dict[str, Any]]], table_summary: str
    ) -> List[str]:
        """Convert multiple rows into natural language text using context from summaries"""
        try:
            # Prepare rows data
            rows_data = [
                {cell["header"]: cell["value"] for cell in row} for row in rows
            ]

            # Get natural language text from LLM with retry
            messages = self.row_text_prompt.format_messages(
                table_summary=table_summary, rows_data=json.dumps(rows_data, indent=2)
            )

            response = await self._call_llm(messages)
            if '</think>' in response.content:
                response.content = response.content.split('</think>')[-1]
            # Parse JSON array from response
            try:
                return json.loads(response.content)
            except json.JSONDecodeError:
                # Fallback handling for non-JSON responses
                content = response.content
                start = content.find("[")
                end = content.rfind("]")
                if start != -1 and end != -1:
                    try:
                        return json.loads(content[start : end + 1])
                    except json.JSONDecodeError:
                        return [content]
                else:
                    return [content]

        except Exception as e:
            self.logger.error(f"❌ Error getting rows text: {str(e)}")
            raise
