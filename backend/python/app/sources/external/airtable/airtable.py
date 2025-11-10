import json
from http import HTTPStatus
from typing import Any, Literal
from urllib.parse import urlencode

from app.sources.client.airtable.airtable import AirtableClient, AirtableResponse
from app.sources.client.http.http_request import HTTPRequest


class AirtableDataSource:
    """Auto-generated Airtable API client wrapper.
    Provides async methods for ALL Airtable API endpoints:
    - Web API (Records, Bases, Tables, Fields, Views)
    - Metadata API (Schema information)
    - Webhooks API (Real-time notifications)
    - Enterprise API (Users, Groups, Collaborators, Admin)
    - Attachments API (File handling)
    - OAuth API (Authentication)
    - Sync API (CSV data synchronization)
    - Audit Log API (Enterprise audit events)
    All methods return AirtableResponse objects with standardized success/data/error format.
    All parameters are explicitly typed - no **kwargs usage.
    """

    def __init__(self, client: AirtableClient) -> None:
        """Initialize with AirtableClient."""
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_data_source(self) -> "AirtableDataSource":
        """Return the data source instance."""
        return self

    async def add_workspace_collaborator(
        self,
        workspace_id: str,
        permission_level: Literal["none", "read", "comment", "edit", "create", "owner"],
        email: str | None = None,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> AirtableResponse:
        """Add collaborator to workspace

        Args:
            workspace_id: Workspace ID (starts with "wsp")
            email: Email address of user to add
            user_id: User ID of user to add
            group_id: Group ID to add
            permission_level: Permission level to grant

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/workspaces/{workspace_id}/collaborators"

        body = {}
        if email is not None:
            body["email"] = email
        if user_id is not None:
            body["user_id"] = user_id
        if group_id is not None:
            body["group_id"] = group_id
        body["permission_level"] = permission_level

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def update_workspace_collaborator(
        self,
        workspace_id: str,
        collaborator_id: str,
        permission_level: Literal["none", "read", "comment", "edit", "create", "owner"],
    ) -> AirtableResponse:
        """Update workspace collaborator permissions

        Args:
            workspace_id: Workspace ID (starts with "wsp")
            collaborator_id: Collaborator ID
            permission_level: New permission level

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/workspaces/{workspace_id}/collaborators/{collaborator_id}"
        )

        body = {}
        body["permission_level"] = permission_level

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def remove_workspace_collaborator(
        self,
        workspace_id: str,
        collaborator_id: str,
    ) -> AirtableResponse:
        """Remove collaborator from workspace

        Args:
            workspace_id: Workspace ID (starts with "wsp")
            collaborator_id: Collaborator ID

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/workspaces/{workspace_id}/collaborators/{collaborator_id}"
        )

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def delete_field(
        self,
        base_id: str,
        table_id: str,
        field_id: str,
    ) -> AirtableResponse:
        """Delete a field from a table

        Args:
            base_id: Base ID (starts with "app")
            table_id: Table ID (starts with "tbl")
            field_id: Field ID (starts with "fld")

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url + f"/meta/bases/{base_id}/tables/{table_id}/fields/{field_id}"
        )

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            if response.status == HTTPStatus.NO_CONTENT:
                return AirtableResponse(success=True, data=None)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_interfaces(
        self,
        base_id: str,
    ) -> AirtableResponse:
        """List interfaces (pages) in a base

        Args:
            base_id: Base ID (starts with "app")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/interfaces"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_interface(
        self,
        base_id: str,
        interface_id: str,
    ) -> AirtableResponse:
        """Get interface details

        Args:
            base_id: Base ID (starts with "app")
            interface_id: Interface ID (starts with "pag")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/interfaces/{interface_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_base_shares(
        self,
        base_id: str,
    ) -> AirtableResponse:
        """List share links for a base

        Args:
            base_id: Base ID (starts with "app")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/shares"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_base_share(
        self,
        base_id: str,
        share_type: Literal["read", "edit"],
        restriction: dict[str, Any] | None = None,
    ) -> AirtableResponse:
        """Create share link for a base

        Args:
            base_id: Base ID (starts with "app")
            share_type: Type of sharing access
            restriction: Access restrictions

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/shares"

        body = {}
        body["share_type"] = share_type
        if restriction is not None:
            body["restriction"] = restriction

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def delete_base_share(
        self,
        base_id: str,
        share_id: str,
    ) -> AirtableResponse:
        """Delete a base share link

        Args:
            base_id: Base ID (starts with "app")
            share_id: Share ID

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/shares/{share_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            if response.status == HTTPStatus.NO_CONTENT:
                return AirtableResponse(success=True, data=None)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def sync_csv_data(
        self,
        base_id: str,
        table_id_or_name: str,
        csv_data: str,
        import_options: dict[str, Any] | None = None,
    ) -> AirtableResponse:
        """Sync CSV data with table (up to 10,000 rows)

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            csv_data: CSV data to sync
            import_options: Import configuration options

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/{base_id}/{table_id_or_name}/sync"

        body = {}
        body["csv_data"] = csv_data
        if import_options is not None:
            body["import_options"] = import_options

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_audit_log_events(
        self,
        enterprise_account_id: str,
        sort_order: Literal["asc", "desc"] | None = None,
        limit: int | None = None,
        next_page: str | None = None,
        origin_ip: str | None = None,
        actor_email: str | None = None,
        event_type: str | None = None,
        occurred_at_gte: str | None = None,
        occurred_at_lte: str | None = None,
    ) -> AirtableResponse:
        """Get audit log events for enterprise

        Args:
            enterprise_account_id: Enterprise account ID
            sort_order: Sort order for events
            limit: Maximum number of events to return
            next_page: Pagination cursor for next page
            origin_ip: Filter by origin IP address
            actor_email: Filter by actor email
            event_type: Filter by event type
            occurred_at_gte: Filter events after this time (ISO 8601)
            occurred_at_lte: Filter events before this time (ISO 8601)

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if sort_order is not None:
            query_params.append(("sort_order", str(sort_order)))
        if limit is not None:
            query_params.append(("limit", str(limit)))
        if next_page is not None:
            query_params.append(("next_page", str(next_page)))
        if origin_ip is not None:
            query_params.append(("origin_ip", str(origin_ip)))
        if actor_email is not None:
            query_params.append(("actor_email", str(actor_email)))
        if event_type is not None:
            query_params.append(("event_type", str(event_type)))
        if occurred_at_gte is not None:
            query_params.append(("occurred_at_gte", str(occurred_at_gte)))
        if occurred_at_lte is not None:
            query_params.append(("occurred_at_lte", str(occurred_at_lte)))

        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/auditLogEvents"
        )
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_bases(
        self,
        offset: str | None = None,
    ) -> AirtableResponse:
        """List all bases accessible to the user

        Args:
            offset: Pagination offset token from previous response

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if offset is not None:
            query_params.append(("offset", str(offset)))

        url = self.base_url + "/meta/bases"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_base_schema(
        self,
        base_id: str,
        include: list[str] | None = None,
    ) -> AirtableResponse:
        """Get base schema including tables, fields, and views

        Args:
            base_id: Base ID (starts with "app")
            include: Additional data to include ("visibleFieldIds")

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if include is not None:
            query_params.append(("include", ",".join(include)))

        url = self.base_url + f"/meta/bases/{base_id}/tables"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_base_collaborators(
        self,
        base_id: str,
    ) -> AirtableResponse:
        """Get base collaborators

        Args:
            base_id: Base ID (starts with "app")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/collaborators"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_records(
        self,
        base_id: str,
        table_id_or_name: str,
        fields: list[str] | None = None,
        filter_by_formula: str | None = None,
        max_records: int | None = None,
        page_size: int | None = None,
        sort: list[dict[str, str]] | None = None,
        view: str | None = None,
        cell_format: Literal["json", "string"] | None = None,
        time_zone: str | None = None,
        user_locale: str | None = None,
        offset: str | None = None,
        return_fields_by_field_id: bool | None = None,
    ) -> AirtableResponse:
        """List records from a table with optional filtering and sorting

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            fields: List of field names or IDs to return
            filter_by_formula: Formula to filter records
            max_records: Maximum number of records to return
            page_size: Number of records per page (max 100)
            sort: List of sort objects with "field" and "direction" keys
            view: View name or ID to use
            cell_format: Cell format: "json" (default) or "string"
            time_zone: Time zone for date/time fields
            user_locale: User locale for formatting
            offset: Pagination offset token
            return_fields_by_field_id: Return field objects with field ID as key

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if fields is not None:
            for field in fields:
                query_params.append(("fields[]", field))
        if filter_by_formula is not None:
            query_params.append(("filterByFormula", str(filter_by_formula)))
        if max_records is not None:
            query_params.append(("maxRecords", str(max_records)))
        if page_size is not None:
            query_params.append(("pageSize", str(page_size)))
        if sort is not None:
            for i, sort_obj in enumerate(sort):
                query_params.append((f"sort[{i}][field]", sort_obj.get("field", "")))
                query_params.append(
                    (f"sort[{i}][direction]", sort_obj.get("direction", "asc"))
                )
        if view is not None:
            query_params.append(("view", str(view)))
        if cell_format is not None:
            query_params.append(("cellFormat", str(cell_format)))
        if time_zone is not None:
            query_params.append(("timeZone", str(time_zone)))
        if user_locale is not None:
            query_params.append(("userLocale", str(user_locale)))
        if offset is not None:
            query_params.append(("offset", str(offset)))
        if return_fields_by_field_id is not None:
            query_params.append(
                (
                    "returnFieldsByFieldId",
                    "true" if return_fields_by_field_id else "false",
                )
            )

        url = self.base_url + f"/{base_id}/{table_id_or_name}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_record(
        self,
        base_id: str,
        table_id_or_name: str,
        record_id: str,
        return_fields_by_field_id: bool | None = None,
    ) -> AirtableResponse:
        """Retrieve a single record

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            record_id: Record ID (starts with "rec")
            return_fields_by_field_id: Return field objects with field ID as key

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if return_fields_by_field_id is not None:
            query_params.append(
                (
                    "returnFieldsByFieldId",
                    "true" if return_fields_by_field_id else "false",
                )
            )

        url = self.base_url + f"/{base_id}/{table_id_or_name}/{record_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_records(
        self,
        base_id: str,
        table_id_or_name: str,
        records: list[dict[str, Any]],
        typecast: bool | None = None,
        return_fields_by_field_id: bool | None = None,
    ) -> AirtableResponse:
        """Create one or more records

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            records: Array of record objects to create
            typecast: Automatic data conversion from string values
            return_fields_by_field_id: Return field objects with field ID as key

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/{base_id}/{table_id_or_name}"

        body = {}
        body["records"] = records
        if typecast is not None:
            body["typecast"] = typecast
        if return_fields_by_field_id is not None:
            body["returnFieldsByFieldId"] = return_fields_by_field_id

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def update_records(
        self,
        base_id: str,
        table_id_or_name: str,
        records: list[dict[str, Any]],
        typecast: bool | None = None,
        return_fields_by_field_id: bool | None = None,
        destructive_update: bool | None = None,
    ) -> AirtableResponse:
        """Update one or more records

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            records: Array of record objects to update
            typecast: Automatic data conversion from string values
            return_fields_by_field_id: Return field objects with field ID as key
            destructive_update: Clear unspecified cell values

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/{base_id}/{table_id_or_name}"

        body = {}
        body["records"] = records
        if typecast is not None:
            body["typecast"] = typecast
        if return_fields_by_field_id is not None:
            body["returnFieldsByFieldId"] = return_fields_by_field_id
        if destructive_update is not None:
            body["destructiveUpdate"] = destructive_update

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def upsert_records(
        self,
        base_id: str,
        table_id_or_name: str,
        perform_upsert: bool,
        records: list[dict[str, Any]],
        fields_to_merge_on: list[str],
        typecast: bool | None = None,
        return_fields_by_field_id: bool | None = None,
    ) -> AirtableResponse:
        """Update or create records using performUpsert

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            perform_upsert: Enable upsert mode
            records: Array of record objects to upsert
            fields_to_merge_on: Fields to use for matching existing records
            typecast: Automatic data conversion from string values
            return_fields_by_field_id: Return field objects with field ID as key

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/{base_id}/{table_id_or_name}"

        body = {}
        body["performUpsert"] = {"fieldsToMergeOn": fields_to_merge_on}
        body["records"] = records
        if typecast is not None:
            body["typecast"] = typecast
        if return_fields_by_field_id is not None:
            body["returnFieldsByFieldId"] = return_fields_by_field_id

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def delete_records(
        self,
        base_id: str,
        table_id_or_name: str,
        records: list[str],
    ) -> AirtableResponse:
        """Delete one or more records

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            records: Array of record IDs to delete

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        for record_id in records:
            query_params.append(("records[]", record_id))

        url = self.base_url + f"/{base_id}/{table_id_or_name}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_tables(
        self,
        base_id: str,
    ) -> AirtableResponse:
        """List all tables in a base

        Args:
            base_id: Base ID (starts with "app")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/tables"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_table(
        self,
        base_id: str,
        name: str,
        fields: list[dict[str, Any]],
        description: str | None = None,
    ) -> AirtableResponse:
        """Create a new table in a base

        Args:
            base_id: Base ID (starts with "app")
            name: Name of the new table
            description: Description of the table
            fields: Array of field objects to create

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/tables"

        body = {}
        body["name"] = name
        if description is not None:
            body["description"] = description
        body["fields"] = fields

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def update_table(
        self,
        base_id: str,
        table_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> AirtableResponse:
        """Update table properties

        Args:
            base_id: Base ID (starts with "app")
            table_id: Table ID (starts with "tbl")
            name: New name for the table
            description: New description for the table

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/tables/{table_id}"

        body = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_field(
        self,
        base_id: str,
        table_id: str,
        name: str,
        type: str,
        description: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> AirtableResponse:
        """Create a new field in a table

        Args:
            base_id: Base ID (starts with "app")
            table_id: Table ID (starts with "tbl")
            name: Name of the new field
            type: Field type (singleLineText, multilineText, etc.)
            description: Description of the field
            options: Field-specific options

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/tables/{table_id}/fields"

        body = {}
        body["name"] = name
        body["type"] = type
        if description is not None:
            body["description"] = description
        if options is not None:
            body["options"] = options

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def update_field(
        self,
        base_id: str,
        table_id: str,
        field_id: str,
        name: str | None = None,
        description: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> AirtableResponse:
        """Update field properties

        Args:
            base_id: Base ID (starts with "app")
            table_id: Table ID (starts with "tbl")
            field_id: Field ID (starts with "fld")
            name: New name for the field
            description: New description for the field
            options: Field-specific options

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url + f"/meta/bases/{base_id}/tables/{table_id}/fields/{field_id}"
        )

        body = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if options is not None:
            body["options"] = options

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_views(
        self,
        base_id: str,
        table_id: str,
    ) -> AirtableResponse:
        """List all views in a table

        Args:
            base_id: Base ID (starts with "app")
            table_id: Table ID (starts with "tbl")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/tables/{table_id}/views"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_webhooks(
        self,
        base_id: str,
    ) -> AirtableResponse:
        """List all webhooks for a base

        Args:
            base_id: Base ID (starts with "app")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/bases/{base_id}/webhooks"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_webhook(
        self,
        base_id: str,
        notification_url: str,
        specification: dict[str, Any],
    ) -> AirtableResponse:
        """Create a new webhook

        Args:
            base_id: Base ID (starts with "app")
            notification_url: URL to receive webhook notifications
            specification: Webhook specification object

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/bases/{base_id}/webhooks"

        body = {}
        body["notification_url"] = notification_url
        body["specification"] = specification

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_webhook(
        self,
        base_id: str,
        webhook_id: str,
    ) -> AirtableResponse:
        """Get webhook details

        Args:
            base_id: Base ID (starts with "app")
            webhook_id: Webhook ID (starts with "ach")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/bases/{base_id}/webhooks/{webhook_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def update_webhook(
        self,
        base_id: str,
        webhook_id: str,
        notification_url: str | None = None,
        specification: dict[str, Any] | None = None,
    ) -> AirtableResponse:
        """Update webhook configuration

        Args:
            base_id: Base ID (starts with "app")
            webhook_id: Webhook ID (starts with "ach")
            notification_url: New notification URL
            specification: Updated webhook specification

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/bases/{base_id}/webhooks/{webhook_id}"

        body = {}
        if notification_url is not None:
            body["notification_url"] = notification_url
        if specification is not None:
            body["specification"] = specification

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def delete_webhook(
        self,
        base_id: str,
        webhook_id: str,
    ) -> AirtableResponse:
        """Delete a webhook

        Args:
            base_id: Base ID (starts with "app")
            webhook_id: Webhook ID (starts with "ach")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/bases/{base_id}/webhooks/{webhook_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            if response.status == HTTPStatus.NO_CONTENT:
                return AirtableResponse(success=True, data=None)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def refresh_webhook(
        self,
        base_id: str,
        webhook_id: str,
    ) -> AirtableResponse:
        """Refresh webhook to extend expiration

        Args:
            base_id: Base ID (starts with "app")
            webhook_id: Webhook ID (starts with "ach")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/bases/{base_id}/webhooks/{webhook_id}/refresh"

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def enable_webhook_notifications(
        self,
        base_id: str,
        webhook_id: str,
    ) -> AirtableResponse:
        """Enable webhook notifications

        Args:
            base_id: Base ID (starts with "app")
            webhook_id: Webhook ID (starts with "ach")

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/bases/{base_id}/webhooks/{webhook_id}/enableNotifications"
        )

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def disable_webhook_notifications(
        self,
        base_id: str,
        webhook_id: str,
    ) -> AirtableResponse:
        """Disable webhook notifications

        Args:
            base_id: Base ID (starts with "app")
            webhook_id: Webhook ID (starts with "ach")

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/bases/{base_id}/webhooks/{webhook_id}/disableNotifications"
        )

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_enterprise_users(
        self,
        enterprise_account_id: str,
        include: list[str] | None = None,
        collaborations: bool | None = None,
        aggregated: bool | None = None,
    ) -> AirtableResponse:
        """List users in enterprise account

        Args:
            enterprise_account_id: Enterprise account ID
            include: Additional data to include
            collaborations: Include collaboration data
            aggregated: Include aggregated values

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if include is not None:
            query_params.append(("include", ",".join(include)))
        if collaborations is not None:
            query_params.append(
                ("collaborations", "true" if collaborations else "false")
            )
        if aggregated is not None:
            query_params.append(("aggregated", "true" if aggregated else "false"))

        url = self.base_url + f"/meta/enterpriseAccounts/{enterprise_account_id}/users"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_enterprise_user(
        self,
        enterprise_account_id: str,
        user_id: str,
        collaborations: bool | None = None,
        aggregated: bool | None = None,
    ) -> AirtableResponse:
        """Get enterprise user details

        Args:
            enterprise_account_id: Enterprise account ID
            user_id: User ID (starts with "usr")
            collaborations: Include collaboration data
            aggregated: Include aggregated values

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if collaborations is not None:
            query_params.append(
                ("collaborations", "true" if collaborations else "false")
            )
        if aggregated is not None:
            query_params.append(("aggregated", "true" if aggregated else "false"))

        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/users/{user_id}"
        )
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def invite_users_by_email(
        self,
        enterprise_account_id: str,
        invites: list[dict[str, Any]],
        is_dry_run: bool | None = None,
    ) -> AirtableResponse:
        """Invite users by email to enterprise

        Args:
            enterprise_account_id: Enterprise account ID
            invites: Array of invitation objects
            is_dry_run: Simulate the invitation process

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/inviteUsersByEmail"
        )

        body = {}
        body["invites"] = invites
        if is_dry_run is not None:
            body["isDryRun"] = is_dry_run

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def delete_users_by_email(
        self,
        enterprise_account_id: str,
        emails: list[str],
    ) -> AirtableResponse:
        """Delete users by email from enterprise

        Args:
            enterprise_account_id: Enterprise account ID
            emails: Array of email addresses to delete

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/deleteUsersByEmail"
        )

        body = {}
        body["emails"] = emails

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def manage_user_membership(
        self,
        enterprise_account_id: str,
        users: dict[str, Literal["managed", "unmanaged"]],
    ) -> AirtableResponse:
        """Manage user membership (managed/unmanaged)

        Args:
            enterprise_account_id: Enterprise account ID
            users: Dict mapping user IDs/emails to desired state

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/claimUsers"
        )

        body = {}
        body["users"] = users

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def grant_admin_access(
        self,
        enterprise_account_id: str,
        users: list[str | dict[str, str]],
    ) -> AirtableResponse:
        """Grant admin access to users

        Args:
            enterprise_account_id: Enterprise account ID
            users: Array of user IDs, emails, or user info objects

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/grantAdminAccess"
        )

        body = {}
        body["users"] = users

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def revoke_admin_access(
        self,
        enterprise_account_id: str,
        users: list[str | dict[str, str]],
    ) -> AirtableResponse:
        """Revoke admin access from users

        Args:
            enterprise_account_id: Enterprise account ID
            users: Array of user IDs, emails, or user info objects

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/revokeAdminAccess"
        )

        body = {}
        body["users"] = users

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def remove_user_from_enterprise(
        self,
        enterprise_account_id: str,
        user_id: str,
        replacement_owner_id: str | None = None,
        remove_from_descendants: bool | None = None,
    ) -> AirtableResponse:
        """Remove user from enterprise account

        Args:
            enterprise_account_id: Enterprise account ID
            user_id: User ID (starts with "usr")
            replacement_owner_id: Replacement owner ID for sole-owned workspaces
            remove_from_descendants: Remove from descendant enterprise accounts

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/users/{user_id}/remove"
        )

        body = {}
        if replacement_owner_id is not None:
            body["replacementOwnerId"] = replacement_owner_id
        if remove_from_descendants is not None:
            body["removeFromDescendants"] = remove_from_descendants

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_user_groups(
        self,
        enterprise_account_id: str,
        collaborations: bool | None = None,
    ) -> AirtableResponse:
        """List user groups in enterprise account

        Args:
            enterprise_account_id: Enterprise account ID
            collaborations: Include collaboration data

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if collaborations is not None:
            query_params.append(
                ("collaborations", "true" if collaborations else "false")
            )

        url = self.base_url + f"/meta/enterpriseAccounts/{enterprise_account_id}/groups"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_user_group(
        self,
        enterprise_account_id: str,
        group_id: str,
        collaborations: bool | None = None,
    ) -> AirtableResponse:
        """Get user group details

        Args:
            enterprise_account_id: Enterprise account ID
            group_id: Group ID (starts with "grp")
            collaborations: Include collaboration data

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if collaborations is not None:
            query_params.append(
                ("collaborations", "true" if collaborations else "false")
            )

        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/groups/{group_id}"
        )
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_user_group(
        self,
        enterprise_account_id: str,
        name: str,
        description: str | None = None,
        members: list[str] | None = None,
    ) -> AirtableResponse:
        """Create a new user group

        Args:
            enterprise_account_id: Enterprise account ID
            name: Name of the group
            description: Description of the group
            members: Array of user IDs to add to group

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/enterpriseAccounts/{enterprise_account_id}/groups"

        body = {}
        body["name"] = name
        if description is not None:
            body["description"] = description
        if members is not None:
            body["members"] = members

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def update_user_group(
        self,
        enterprise_account_id: str,
        group_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> AirtableResponse:
        """Update user group properties

        Args:
            enterprise_account_id: Enterprise account ID
            group_id: Group ID (starts with "grp")
            name: New name for the group
            description: New description for the group

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/groups/{group_id}"
        )

        body = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def delete_user_group(
        self,
        enterprise_account_id: str,
        group_id: str,
    ) -> AirtableResponse:
        """Delete a user group

        Args:
            enterprise_account_id: Enterprise account ID
            group_id: Group ID (starts with "grp")

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/groups/{group_id}"
        )

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def add_base_collaborator(
        self,
        base_id: str,
        permission_level: Literal["none", "read", "comment", "edit", "create"],
        email: str | None = None,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> AirtableResponse:
        """Add collaborator to base

        Args:
            base_id: Base ID (starts with "app")
            email: Email address of user to add
            user_id: User ID of user to add
            group_id: Group ID to add
            permission_level: Permission level to grant

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/collaborators"

        body = {}
        if email is not None:
            body["email"] = email
        if user_id is not None:
            body["user_id"] = user_id
        if group_id is not None:
            body["group_id"] = group_id
        body["permission_level"] = permission_level

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def update_base_collaborator(
        self,
        base_id: str,
        collaborator_id: str,
        permission_level: Literal["none", "read", "comment", "edit", "create"],
    ) -> AirtableResponse:
        """Update base collaborator permissions

        Args:
            base_id: Base ID (starts with "app")
            collaborator_id: Collaborator ID
            permission_level: New permission level

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/collaborators/{collaborator_id}"

        body = {}
        body["permission_level"] = permission_level

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="PATCH",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def remove_base_collaborator(
        self,
        base_id: str,
        collaborator_id: str,
    ) -> AirtableResponse:
        """Remove collaborator from base

        Args:
            base_id: Base ID (starts with "app")
            collaborator_id: Collaborator ID

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/bases/{base_id}/collaborators/{collaborator_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def list_workspaces(
        self,
        enterprise_account_id: str,
    ) -> AirtableResponse:
        """List workspaces in enterprise account

        Args:
            enterprise_account_id: Enterprise account ID

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/workspaces"
        )

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_workspace(
        self,
        workspace_id: str,
    ) -> AirtableResponse:
        """Get workspace details

        Args:
            workspace_id: Workspace ID (starts with "wsp")

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + f"/meta/workspaces/{workspace_id}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_enterprise_info(
        self,
        enterprise_account_id: str,
        include: list[str] | None = None,
    ) -> AirtableResponse:
        """Get enterprise account information

        Args:
            enterprise_account_id: Enterprise account ID
            include: Additional data to include ("aggregated", "descendants")

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if include is not None:
            query_params.append(("include", ",".join(include)))

        url = self.base_url + f"/meta/enterpriseAccounts/{enterprise_account_id}"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_descendant_enterprise(
        self,
        enterprise_account_id: str,
        name: str,
    ) -> AirtableResponse:
        """Create descendant enterprise account

        Args:
            enterprise_account_id: Parent enterprise account ID
            name: Name of the descendant enterprise

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/meta/enterpriseAccounts/{enterprise_account_id}/descendants"
        )

        body = {}
        body["name"] = name

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def create_attachment(
        self,
        base_id: str,
        table_id_or_name: str,
        record_id: str,
        field_id: str,
        content_type: str,
        filename: str,
        file: bytes | str,
    ) -> AirtableResponse:
        """Upload attachment to a record field

        Args:
            base_id: Base ID (starts with "app")
            table_id_or_name: Table ID (starts with "tbl") or table name
            record_id: Record ID (starts with "rec")
            field_id: Field ID (starts with "fld")
            content_type: MIME type of the file
            filename: Name of the file
            file: File content (binary data or base64 string)

        Returns:
            AirtableResponse with operation result

        """
        url = (
            self.base_url
            + f"/{base_id}/{table_id_or_name}/{record_id}/{field_id}/uploadAttachment"
        )

        body = {}
        body["contentType"] = content_type
        body["filename"] = filename
        body["file"] = file

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def get_current_user(
        self,
    ) -> AirtableResponse:
        """Get current user information and scopes

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + "/meta/whoami"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def oauth_authorize(
        self,
        client_id: str,
        redirect_uri: str,
        response_type: str,
        scope: str,
        state: str | None = None,
    ) -> AirtableResponse:
        """OAuth authorization endpoint

        Args:
            client_id: OAuth client ID
            redirect_uri: Redirect URI
            response_type: Response type (code)
            scope: OAuth scopes
            state: State parameter for security

        Returns:
            AirtableResponse with operation result

        """
        query_params = []
        if client_id is not None:
            query_params.append(("client_id", str(client_id)))
        if redirect_uri is not None:
            query_params.append(("redirect_uri", str(redirect_uri)))
        if response_type is not None:
            query_params.append(("response_type", str(response_type)))
        if scope is not None:
            query_params.append(("scope", str(scope)))
        if state is not None:
            query_params.append(("state", str(state)))

        url = self.base_url + "/oauth2/v1/authorize"
        if query_params:
            query_string = urlencode(query_params)
            url += f"?{query_string}"

        headers = self.http.headers.copy()

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    async def oauth_token(
        self,
        grant_type: str,
        code: str | None = None,
        redirect_uri: str | None = None,
        refresh_token: str | None = None,
    ) -> AirtableResponse:
        """OAuth token exchange endpoint

        Args:
            grant_type: Grant type (authorization_code, refresh_token)
            code: Authorization code (for authorization_code grant)
            redirect_uri: Redirect URI (for authorization_code grant)
            refresh_token: Refresh token (for refresh_token grant)

        Returns:
            AirtableResponse with operation result

        """
        url = self.base_url + "/oauth2/v1/token"

        body = {}
        body["grant_type"] = grant_type
        if code is not None:
            body["code"] = code
        if redirect_uri is not None:
            body["redirect_uri"] = redirect_uri
        if refresh_token is not None:
            body["refresh_token"] = refresh_token

        headers = self.http.headers.copy()
        headers["Content-Type"] = "application/json"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            body=json.dumps(body),
        )

        try:
            response = await self.http.execute(request)
            return AirtableResponse(success=True, data=response.json())
        except Exception as e:
            return AirtableResponse(success=False, error=str(e))

    def get_client_info(self) -> AirtableResponse:
        """Get information about the Airtable client."""
        info = {
            "total_methods": 58,
            "base_url": self.base_url,
            "api_categories": [
                "Web API (Records, Bases, Tables, Fields, Views)",
                "Metadata API (Schema information)",
                "Webhooks API (Real-time notifications)",
                "Enterprise API (Users, Groups, Collaborators, Admin)",
                "Attachments API (File handling)",
                "OAuth API (Authentication)",
            ],
        }
        return AirtableResponse(success=True, data=info)
