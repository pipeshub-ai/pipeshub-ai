import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.airtable.airtable import AirtableDataSource

logger = logging.getLogger(__name__)


class Airtable:
    """Airtable tool exposed to the agents"""
    def __init__(self, client: object) -> None:
        """Initialize the Airtable tool"""
        """
        Args:
            client: Airtable client object
        Returns:
            None
        """
        self.client = AirtableDataSource(client)

    def _run_async(self, coro):
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

    @tool(
        app_name="airtable",
        tool_name="get_records",
        description="Get records from an Airtable table",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="ID of the Airtable base",
                required=True
            ),
            ToolParameter(
                name="table_name",
                type=ParameterType.STRING,
                description="Name of the table",
                required=True
            ),
            ToolParameter(
                name="max_records",
                type=ParameterType.INTEGER,
                description="Maximum number of records to return",
                required=False
            ),
            ToolParameter(
                name="view",
                type=ParameterType.STRING,
                description="View to use for filtering",
                required=False
            ),
            ToolParameter(
                name="filter_by_formula",
                type=ParameterType.STRING,
                description="Formula to filter records",
                required=False
            )
        ]
    )
    def get_records(
        self,
        base_id: str,
        table_name: str,
        max_records: Optional[int] = None,
        view: Optional[str] = None,
        filter_by_formula: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Get records from an Airtable table"""
        """
        Args:
            base_id: ID of the Airtable base
            table_name: Name of the table
            max_records: Maximum number of records to return
            view: View to use for filtering
            filter_by_formula: Formula to filter records
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AirtableDataSource method
            response = self._run_async(self.client.get_records(
                base_id=base_id,
                table_name=table_name,
                max_records=max_records,
                view=view,
                filter_by_formula=filter_by_formula
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting records: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="get_record",
        description="Get a specific record from an Airtable table",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="ID of the Airtable base",
                required=True
            ),
            ToolParameter(
                name="table_name",
                type=ParameterType.STRING,
                description="Name of the table",
                required=True
            ),
            ToolParameter(
                name="record_id",
                type=ParameterType.STRING,
                description="ID of the record",
                required=True
            )
        ]
    )
    def get_record(
        self,
        base_id: str,
        table_name: str,
        record_id: str
    ) -> Tuple[bool, str]:
        """Get a specific record from an Airtable table"""
        """
        Args:
            base_id: ID of the Airtable base
            table_name: Name of the table
            record_id: ID of the record
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AirtableDataSource method
            response = self._run_async(self.client.get_record(
                base_id=base_id,
                table_name=table_name,
                record_id=record_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting record: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="create_record",
        description="Create a new record in an Airtable table",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="ID of the Airtable base",
                required=True
            ),
            ToolParameter(
                name="table_name",
                type=ParameterType.STRING,
                description="Name of the table",
                required=True
            ),
            ToolParameter(
                name="fields",
                type=ParameterType.STRING,
                description="Fields for the new record (JSON string)",
                required=True
            )
        ]
    )
    def create_record(
        self,
        base_id: str,
        table_name: str,
        fields: str
    ) -> Tuple[bool, str]:
        """Create a new record in an Airtable table"""
        """
        Args:
            base_id: ID of the Airtable base
            table_name: Name of the table
            fields: Fields for the new record (JSON string)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Parse fields from JSON string
            fields_dict = json.loads(fields)

            # Use AirtableDataSource method
            response = self._run_async(self.client.create_record(
                base_id=base_id,
                table_name=table_name,
                fields=fields_dict
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error creating record: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="update_record",
        description="Update a record in an Airtable table",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="ID of the Airtable base",
                required=True
            ),
            ToolParameter(
                name="table_name",
                type=ParameterType.STRING,
                description="Name of the table",
                required=True
            ),
            ToolParameter(
                name="record_id",
                type=ParameterType.STRING,
                description="ID of the record",
                required=True
            ),
            ToolParameter(
                name="fields",
                type=ParameterType.STRING,
                description="Fields to update (JSON string)",
                required=True
            )
        ]
    )
    def update_record(
        self,
        base_id: str,
        table_name: str,
        record_id: str,
        fields: str
    ) -> Tuple[bool, str]:
        """Update a record in an Airtable table"""
        """
        Args:
            base_id: ID of the Airtable base
            table_name: Name of the table
            record_id: ID of the record
            fields: Fields to update (JSON string)
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Parse fields from JSON string
            fields_dict = json.loads(fields)

            # Use AirtableDataSource method
            response = self._run_async(self.client.update_record(
                base_id=base_id,
                table_name=table_name,
                record_id=record_id,
                fields=fields_dict
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error updating record: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="delete_record",
        description="Delete a record from an Airtable table",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="ID of the Airtable base",
                required=True
            ),
            ToolParameter(
                name="table_name",
                type=ParameterType.STRING,
                description="Name of the table",
                required=True
            ),
            ToolParameter(
                name="record_id",
                type=ParameterType.STRING,
                description="ID of the record",
                required=True
            )
        ]
    )
    def delete_record(
        self,
        base_id: str,
        table_name: str,
        record_id: str
    ) -> Tuple[bool, str]:
        """Delete a record from an Airtable table"""
        """
        Args:
            base_id: ID of the Airtable base
            table_name: Name of the table
            record_id: ID of the record
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AirtableDataSource method
            response = self._run_async(self.client.delete_record(
                base_id=base_id,
                table_name=table_name,
                record_id=record_id
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting record: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="list_bases",
        description="List all Airtable bases",
        parameters=[]
    )
    def list_bases(self) -> Tuple[bool, str]:
        """List all Airtable bases"""
        """
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AirtableDataSource method
            response = self._run_async(self.client.list_bases())

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing bases: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="airtable",
        tool_name="get_base_schema",
        description="Get the schema of an Airtable base",
        parameters=[
            ToolParameter(
                name="base_id",
                type=ParameterType.STRING,
                description="ID of the Airtable base",
                required=True
            )
        ]
    )
    def get_base_schema(self, base_id: str) -> Tuple[bool, str]:
        """Get the schema of an Airtable base"""
        """
        Args:
            base_id: ID of the Airtable base
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use AirtableDataSource method
            response = self._run_async(self.client.get_base_schema(base_id=base_id))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting base schema: {e}")
            return False, json.dumps({"error": str(e)})
