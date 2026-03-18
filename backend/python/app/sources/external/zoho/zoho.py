"""
Zoho CRM DataSource - API wrapper using the official Zoho CRM SDK.

Provides typed wrapper methods for common Zoho CRM operations including
records, modules, users, roles, profiles, and organizations.

All methods return ZohoResponse objects.
"""

from __future__ import annotations

from typing import Any, cast

from app.sources.client.zoho.zoho import ZohoClient, ZohoClientViaOAuth, ZohoResponse


class ZohoDataSource:
    """Zoho CRM DataSource

    Typed wrapper over the Zoho CRM SDK for common business operations.

    Accepts either a ZohoClient or a ZohoClientViaOAuth instance.

    Coverage:
    - Records: list, get, create, update, delete, search
    - Modules: list, get
    - Users: list, get
    - Roles: list
    - Profiles: list
    - Organizations: list, get
    """

    def __init__(self, client_or_wrapper: ZohoClient | ZohoClientViaOAuth) -> None:
        """Initialize with a Zoho CRM client.

        Args:
            client_or_wrapper: ZohoClient or ZohoClientViaOAuth instance
        """
        if isinstance(client_or_wrapper, ZohoClient):
            self._sdk: ZohoClientViaOAuth = client_or_wrapper.get_client()
        else:
            self._sdk = client_or_wrapper
        self._sdk.ensure_initialized()

    def get_data_source(self) -> "ZohoDataSource":
        """Return the data source instance."""
        return self

    # =========================================================================
    # RECORD OPERATIONS
    # =========================================================================

    def list_records(
        self,
        module: str,
        page: int | None = None,
        per_page: int | None = None,
        fields: list[str] | None = None,
    ) -> ZohoResponse:
        """List records from a module.

        Args:
            module: Module API name (e.g., 'Leads', 'Contacts', 'Deals')
            page: Page number (1-based)
            per_page: Records per page (max 200)
            fields: List of field API names to return

        Returns:
            ZohoResponse with list of records
        """
        try:
            record_ops = self._sdk.get_record_operations(module)
            param_instance = self._build_get_records_param(page, per_page, fields)
            resp =record_ops.get_records(param_instance)  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "list_records")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to list records"
            )

    def get_record(self, module: str, record_id: str) -> ZohoResponse:
        """Get a single record by ID.

        Args:
            module: Module API name
            record_id: Record ID

        Returns:
            ZohoResponse with record data
        """
        try:
            record_ops = self._sdk.get_record_operations(module)
            resp =record_ops.get_record(int(record_id))  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "get_record")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to get record"
            )

    def create_record(
        self,
        module: str,
        data: dict[str, Any],
    ) -> ZohoResponse:
        """Create a record in a module.

        Args:
            module: Module API name
            data: Record field data as a dictionary

        Returns:
            ZohoResponse with created record info
        """
        try:
            from zohocrmsdk.src.com.zoho.crm.api.record import (  # type: ignore[import-untyped]
                BodyWrapper,
                Record,
            )

            record = Record()  # type: ignore[no-untyped-call]
            for key, value in data.items():
                record.add_key_value(key, value)  # type: ignore[no-untyped-call]

            body = BodyWrapper()  # type: ignore[no-untyped-call]
            body.set_data([record])  # type: ignore[no-untyped-call]

            record_ops = self._sdk.get_record_operations(module)
            resp =record_ops.create_records(body)  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "create_record")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to create record"
            )

    def update_record(
        self,
        module: str,
        record_id: str,
        data: dict[str, Any],
    ) -> ZohoResponse:
        """Update a record in a module.

        Args:
            module: Module API name
            record_id: Record ID to update
            data: Record field data to update

        Returns:
            ZohoResponse with updated record info
        """
        try:
            from zohocrmsdk.src.com.zoho.crm.api.record import (  # type: ignore[import-untyped]
                BodyWrapper,
                Record,
            )

            record = Record()  # type: ignore[no-untyped-call]
            record.set_id(int(record_id))  # type: ignore[no-untyped-call]
            for key, value in data.items():
                record.add_key_value(key, value)  # type: ignore[no-untyped-call]

            body = BodyWrapper()  # type: ignore[no-untyped-call]
            body.set_data([record])  # type: ignore[no-untyped-call]

            record_ops = self._sdk.get_record_operations(module)
            resp =record_ops.update_records(body)  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "update_record")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to update record"
            )

    def delete_record(self, module: str, record_id: str) -> ZohoResponse:
        """Delete a record from a module.

        Args:
            module: Module API name
            record_id: Record ID to delete

        Returns:
            ZohoResponse with deletion result
        """
        try:
            from zohocrmsdk.src.com.zoho.crm.api import (
                ParameterMap,  # type: ignore[import-untyped]
            )
            from zohocrmsdk.src.com.zoho.crm.api.record import (  # type: ignore[import-untyped]
                DeleteRecordsParam,
            )

            param = ParameterMap()  # type: ignore[no-untyped-call]
            param.add(DeleteRecordsParam.ids, record_id)  # type: ignore[no-untyped-call]

            record_ops = self._sdk.get_record_operations(module)
            resp =record_ops.delete_records(param)  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "delete_record")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to delete record"
            )

    def search_records(
        self,
        module: str,
        criteria: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        word: str | None = None,
        page: int | None = None,
        per_page: int | None = None,
    ) -> ZohoResponse:
        """Search records in a module.

        Args:
            module: Module API name
            criteria: Search criteria string (e.g., '(Last_Name:equals:Burns)')
            email: Search by email
            phone: Search by phone
            word: Search by keyword
            page: Page number
            per_page: Records per page

        Returns:
            ZohoResponse with matching records
        """
        try:
            from zohocrmsdk.src.com.zoho.crm.api import (
                ParameterMap,  # type: ignore[import-untyped]
            )
            from zohocrmsdk.src.com.zoho.crm.api.record import (  # type: ignore[import-untyped]
                SearchRecordsParam,
            )

            param = ParameterMap()  # type: ignore[no-untyped-call]
            if criteria:
                param.add(SearchRecordsParam.criteria, criteria)  # type: ignore[no-untyped-call]
            if email:
                param.add(SearchRecordsParam.email, email)  # type: ignore[no-untyped-call]
            if phone:
                param.add(SearchRecordsParam.phone, phone)  # type: ignore[no-untyped-call]
            if word:
                param.add(SearchRecordsParam.word, word)  # type: ignore[no-untyped-call]
            if page is not None:
                param.add(SearchRecordsParam.page, page)  # type: ignore[no-untyped-call]
            if per_page is not None:
                param.add(SearchRecordsParam.per_page, per_page)  # type: ignore[no-untyped-call]

            record_ops = self._sdk.get_record_operations(module)
            resp =record_ops.search_records(param)  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "search_records")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to search records"
            )

    # =========================================================================
    # MODULE OPERATIONS
    # =========================================================================

    def list_modules(self) -> ZohoResponse:
        """List all available modules.

        Returns:
            ZohoResponse with list of modules
        """
        try:
            modules_ops = self._sdk.get_modules_operations()
            resp =modules_ops.get_modules()  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "list_modules")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to list modules"
            )

    def get_module(self, module_api_name: str) -> ZohoResponse:
        """Get details of a specific module.

        Args:
            module_api_name: Module API name

        Returns:
            ZohoResponse with module details
        """
        try:
            modules_ops = self._sdk.get_modules_operations()
            resp =modules_ops.get_module(module_api_name)  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "get_module")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to get module"
            )

    # =========================================================================
    # USER OPERATIONS
    # =========================================================================

    def list_users(self, user_type: str | None = None) -> ZohoResponse:
        """List users in the organization.

        Args:
            user_type: Filter by user type (e.g., 'AllUsers', 'ActiveUsers',
                       'DeactiveUsers', 'AdminUsers')

        Returns:
            ZohoResponse with list of users
        """
        try:
            from zohocrmsdk.src.com.zoho.crm.api import (
                ParameterMap,  # type: ignore[import-untyped]
            )
            from zohocrmsdk.src.com.zoho.crm.api.users import (
                GetUsersParam,  # type: ignore[import-untyped]
            )

            users_ops = self._sdk.get_users_operations()

            param = ParameterMap()  # type: ignore[no-untyped-call]
            if user_type:
                param.add(GetUsersParam.type, user_type)  # type: ignore[no-untyped-call]

            resp =users_ops.get_users(param)  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "list_users")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to list users"
            )

    def get_user(self, user_id: str) -> ZohoResponse:
        """Get a single user by ID.

        Args:
            user_id: User ID

        Returns:
            ZohoResponse with user details
        """
        try:
            users_ops = self._sdk.get_users_operations()
            resp =users_ops.get_user(int(user_id))  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "get_user")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to get user"
            )

    # =========================================================================
    # ROLE OPERATIONS
    # =========================================================================

    def list_roles(self) -> ZohoResponse:
        """List all roles in the organization.

        Returns:
            ZohoResponse with list of roles
        """
        try:
            roles_ops = self._sdk.get_roles_operations()
            resp =roles_ops.get_roles()  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "list_roles")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to list roles"
            )

    # =========================================================================
    # PROFILE OPERATIONS
    # =========================================================================

    def list_profiles(self) -> ZohoResponse:
        """List all profiles in the organization.

        Returns:
            ZohoResponse with list of profiles
        """
        try:
            profiles_ops = self._sdk.get_profiles_operations()
            resp =profiles_ops.get_profiles()  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "list_profiles")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False, error=str(e), message="Failed to list profiles"
            )

    # =========================================================================
    # ORGANIZATION OPERATIONS
    # =========================================================================

    def list_organizations(self) -> ZohoResponse:
        """List organization information.

        Note: Zoho CRM returns a single organization. This method wraps
        get_organization for API consistency.

        Returns:
            ZohoResponse with organization data
        """
        return self.get_organization()

    def get_organization(self) -> ZohoResponse:
        """Get the current organization details.

        Returns:
            ZohoResponse with organization details
        """
        try:
            org_ops = self._sdk.get_org_operations()
            resp =org_ops.get_organization()  # type: ignore[no-untyped-call]
            return self._parse_sdk_response(resp, "get_organization")  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            return ZohoResponse(
                success=False,
                error=str(e),
                message="Failed to get organization",
            )

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _build_get_records_param(
        page: int | None = None,
        per_page: int | None = None,
        fields: list[str] | None = None,
    ) -> object:
        """Build a ParameterMap for get_records.

        Args:
            page: Page number
            per_page: Records per page
            fields: List of field API names

        Returns:
            ParameterMap instance
        """
        from zohocrmsdk.src.com.zoho.crm.api import (
            ParameterMap,  # type: ignore[import-untyped]
        )
        from zohocrmsdk.src.com.zoho.crm.api.record import (
            GetRecordsParam,  # type: ignore[import-untyped]
        )

        param = ParameterMap()  # type: ignore[no-untyped-call]
        if page is not None:
            param.add(GetRecordsParam.page, page)  # type: ignore[no-untyped-call]
        if per_page is not None:
            param.add(GetRecordsParam.per_page, per_page)  # type: ignore[no-untyped-call]
        if fields:
            param.add(GetRecordsParam.fields, ",".join(fields))  # type: ignore[no-untyped-call]
        return param

    @staticmethod
    def _parse_sdk_response(response: object, operation: str) -> ZohoResponse:
        """Parse a Zoho CRM SDK response into a ZohoResponse.

        The SDK returns an APIResponse object with status_code and object fields.

        Args:
            response: SDK API response object
            operation: Name of the operation for error messages

        Returns:
            ZohoResponse instance
        """
        if response is None:
            return ZohoResponse(
                success=False,
                message=f"No response from {operation}",
            )

        try:
            status_code = getattr(response, "status_code", None)
            if callable(status_code):
                status_code = status_code()

            response_object = getattr(response, "object", None)
            if callable(response_object):
                response_object = response_object()

            is_success = status_code is not None and int(cast(Any, status_code)) < 400

            # Try to extract data from the response object
            data: dict[str, object] | list[object] | None = None
            if response_object is not None:
                if hasattr(response_object, "get_data"):
                    raw_data = response_object.get_data()  # type: ignore[union-attr]
                    if isinstance(raw_data, list):
                        data = cast(list[object], raw_data)
                    elif isinstance(raw_data, dict):
                        data = cast(dict[str, object], raw_data)
                    else:
                        data = {"result": raw_data}
                elif hasattr(response_object, "__dict__"):
                    data = cast(dict[str, object], vars(response_object))
                else:
                    data = {"result": response_object}

            return ZohoResponse(
                success=is_success,
                data=data,
                message=(
                    f"Successfully executed {operation}"
                    if is_success
                    else f"Failed to execute {operation} (status: {status_code})"
                ),
            )
        except Exception as e:
            return ZohoResponse(
                success=False,
                error=str(e),
                message=f"Failed to parse response from {operation}",
            )
