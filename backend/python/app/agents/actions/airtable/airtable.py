import asyncio
import json
import logging
import threading
from typing import Coroutine, Dict, List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.airtable.airtable import AirtableClient
from app.sources.external.airtable.airtable import AirtableDataSource

logger = logging.getLogger(__name__)


class Airtable:
    """Airtable tools exposed to agents using AirtableDataSource.

    This mirrors the structure used by the Confluence actions, providing
    a small, clean surface area of CRUD operations and a search helper.
    """

    def __init__(self, client: AirtableClient) -> None:
        """Initialize the Airtable tool with a data source wrapper.

        Args:
            client: An initialized `AirtableClient` instance
        """
        self.client = AirtableDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"Airtable shutdown encountered an issue: {exc}")

    def _run_async(self, coro: Coroutine[None, None, object]) -> object:
        """Run a coroutine safely from sync context via a dedicated loop."""
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def _handle_response(
        self,
        success: bool,
        data: Optional[dict],
        error: Optional[str],
        success_message: str
    ) -> Tuple[bool, str]:
        """Standardize return shape (success flag, JSON string)."""
        if success:
            return True, json.dumps({
                "message": success_message,
                "data": data or {}
            })
        return False, json.dumps({
            "error": error or "Unknown error"
        })

    @tool(
        app_name="airtable",
        tool_name="create_records",
        description="Create one or more records in an Airtable table",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="Base ID (starts with 'app')"
            ),
            ToolParameter(
                name="table_id_or_name",
                type=ParameterType.STRING,
                description="Table ID (starts with 'tbl') or table name"
            ),
            ToolParameter(
                name="records_json",
                type=ParameterType.STRING,
                description="JSON array of record objects: [{\"fields\": { ... }}]"
            ),
            ToolParameter(
                name="typecast",
                type=ParameterType.BOOLEAN,
                description="Enable Airtable typecasting",
                required=False
            )
        ],
        returns="JSON with creation result"
    )
    def create_records(
        self,
        base_id: str,
        table_id_or_name: str,
        records_json: str,
        typecast: Optional[bool] = None
    ) -> Tuple[bool, str]:
        try:
            try:
                records: List[Dict[str, object]] = json.loads(records_json)
                if not isinstance(records, list):
                    raise ValueError("records_json must be a JSON array")
            except json.JSONDecodeError as exc:
                return False, json.dumps({"error": f"Invalid JSON for records_json: {exc}"})

            resp = self._run_async(
                self.client.create_records(
                    base_id=base_id,
                    table_id_or_name=table_id_or_name,
                    records=records,
                    typecast=typecast
                )
            )
            # resp is AirtableResponse
            return self._handle_response(
                getattr(resp, "success", False),
                getattr(resp, "data", None),
                getattr(resp, "error", None),
                "Records created successfully"
            )
        except Exception as e:
            logger.error(f"Error creating records: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="get_record",
        description="Retrieve a single record by record ID",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="Base ID (starts with 'app')"
            ),
            ToolParameter(
                name="table_id_or_name",
                type=ParameterType.STRING,
                description="Table ID (starts with 'tbl') or table name"
            ),
            ToolParameter(
                name="record_id",
                type=ParameterType.STRING,
                description="Record ID (starts with 'rec')"
            )
        ],
        returns="JSON with record data"
    )
    def get_record(
        self,
        base_id: str,
        table_id_or_name: str,
        record_id: str
    ) -> Tuple[bool, str]:
        try:
            resp = self._run_async(
                self.client.get_record(
                    base_id=base_id,
                    table_id_or_name=table_id_or_name,
                    record_id=record_id
                )
            )
            return self._handle_response(
                getattr(resp, "success", False),
                getattr(resp, "data", None),
                getattr(resp, "error", None),
                "Record fetched successfully"
            )
        except Exception as e:
            logger.error(f"Error getting record: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="list_records",
        description="List records with optional view or formula filtering",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="Base ID (starts with 'app')"
            ),
            ToolParameter(
                name="table_id_or_name",
                type=ParameterType.STRING,
                description="Table ID (starts with 'tbl') or table name"
            ),
            ToolParameter(
                name="view",
                type=ParameterType.STRING,
                description="View name or ID to use",
                required=False
            ),
            ToolParameter(
                name="filter_by_formula",
                type=ParameterType.STRING,
                description="Airtable formula for filtering",
                required=False
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.NUMBER,
                description="Number of records to fetch (max 100)",
                required=False
            )
        ],
        returns="JSON with list of records"
    )
    def list_records(
        self,
        base_id: str,
        table_id_or_name: str,
        view: Optional[str] = None,
        filter_by_formula: Optional[str] = None,
        page_size: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            resp = self._run_async(
                self.client.list_records(
                    base_id=base_id,
                    table_id_or_name=table_id_or_name,
                    view=view,
                    filter_by_formula=filter_by_formula,
                    page_size=page_size
                )
            )
            return self._handle_response(
                getattr(resp, "success", False),
                getattr(resp, "data", None),
                getattr(resp, "error", None),
                "Records fetched successfully"
            )
        except Exception as e:
            logger.error(f"Error listing records: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="update_records",
        description="Update one or more records by ID",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="Base ID (starts with 'app')"
            ),
            ToolParameter(
                name="table_id_or_name",
                type=ParameterType.STRING,
                description="Table ID (starts with 'tbl') or table name"
            ),
            ToolParameter(
                name="records_json",
                type=ParameterType.STRING,
                description="JSON array of records with id and fields"
            ),
            ToolParameter(
                name="typecast",
                type=ParameterType.BOOLEAN,
                description="Enable Airtable typecasting",
                required=False
            ),
            ToolParameter(
                name="destructive_update",
                type=ParameterType.BOOLEAN,
                description="Clear unspecified cell values",
                required=False
            )
        ],
        returns="JSON with update result"
    )
    def update_records(
        self,
        base_id: str,
        table_id_or_name: str,
        records_json: str,
        typecast: Optional[bool] = None,
        destructive_update: Optional[bool] = None
    ) -> Tuple[bool, str]:
        try:
            try:
                records: List[Dict[str, object]] = json.loads(records_json)
                if not isinstance(records, list):
                    raise ValueError("records_json must be a JSON array")
            except json.JSONDecodeError as exc:
                return False, json.dumps({"error": f"Invalid JSON for records_json: {exc}"})

            resp = self._run_async(
                self.client.update_records(
                    base_id=base_id,
                    table_id_or_name=table_id_or_name,
                    records=records,
                    typecast=typecast,
                    destructive_update=destructive_update
                )
            )
            return self._handle_response(
                getattr(resp, "success", False),
                getattr(resp, "data", None),
                getattr(resp, "error", None),
                "Records updated successfully"
            )
        except Exception as e:
            logger.error(f"Error updating records: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="delete_records",
        description="Delete one or more records by ID",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="Base ID (starts with 'app')"
            ),
            ToolParameter(
                name="table_id_or_name",
                type=ParameterType.STRING,
                description="Table ID (starts with 'tbl') or table name"
            ),
            ToolParameter(
                name="record_ids",
                type=ParameterType.STRING,
                description="Comma-separated record IDs (e.g. rec1,rec2)"
            )
        ],
        returns="JSON with deletion result"
    )
    def delete_records(
        self,
        base_id: str,
        table_id_or_name: str,
        record_ids: str
    ) -> Tuple[bool, str]:
        try:
            records = [rid.strip() for rid in record_ids.split(",") if rid.strip()]
            if not records:
                return False, json.dumps({"error": "No record IDs provided"})

            resp = self._run_async(
                self.client.delete_records(
                    base_id=base_id,
                    table_id_or_name=table_id_or_name,
                    records=records
                )
            )
            return self._handle_response(
                getattr(resp, "success", False),
                getattr(resp, "data", None),
                getattr(resp, "error", None),
                "Records deleted successfully"
            )
        except Exception as e:
            logger.error(f"Error deleting records: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="search_records",
        description="Search records using an Airtable formula",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="Base ID (starts with 'app')"
            ),
            ToolParameter(
                name="table_id_or_name",
                type=ParameterType.STRING,
                description="Table ID (starts with 'tbl') or table name"
            ),
            ToolParameter(
                name="filter_by_formula",
                type=ParameterType.STRING,
                description="Airtable formula to filter records"
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.NUMBER,
                description="Number of records to fetch (max 100)",
                required=False
            )
        ],
        returns="JSON with search results"
    )
    def search_records(
        self,
        base_id: str,
        table_id_or_name: str,
        filter_by_formula: str,
        page_size: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            resp = self._run_async(
                self.client.list_records(
                    base_id=base_id,
                    table_id_or_name=table_id_or_name,
                    filter_by_formula=filter_by_formula,
                    page_size=page_size
                )
            )
            return self._handle_response(
                getattr(resp, "success", False),
                getattr(resp, "data", None),
                getattr(resp, "error", None),
                "Search completed successfully"
            )
        except Exception as e:
            logger.error(f"Error searching records: {e}")
            return False, json.dumps({"error": str(e)})
