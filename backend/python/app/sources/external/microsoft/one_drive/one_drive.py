    # Auto-generated Microsoft Onedrive API client.
    # ruff: noqa: BLE001, D417, E501, ANN003, PGH003, PLR0912, C901, PLR0913, G004, TRY400, TRY003, EM101, D100, INP001

import keyword as _kw
import logging
import re
from collections.abc import Mapping

from app.sources.client.microsoft.microsoft import MSGraphClient, MSGraphResponse

# Set up logger
logger = logging.getLogger(__name__)

class OneDriveDataSource:
    """Auto-generated Microsoft OneDrive API client wrapper.

    - Uses Microsoft Graph SDK client internally
    - Snake_case method names for all Microsoft OneDrive API operations
    - Standardized MSGraphResponse format for all responses
    - No direct HTTP calls - all requests go through Graph SDK
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client."""
        self.client = client.get_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Microsoft OneDrive API client initialized successfully")

    def _sanitize_py_name(self, name: str) -> str:
        """Runtime-safe version of sanitize_py_name for mirroring param keys.

        Turns '$select' -> 'dollar_select', 'message-id' -> 'message_id', etc.
        """
        n = name.replace("$", "dollar_").replace("@", "at_").replace(".", "_")
        n = re.sub(r"[^0-9a-zA-Z_]", "_", n)
        if n and n[0].isdigit():
            n = f"_{n}"
        if _kw.iskeyword(n):
            n += "_"
        if n.startswith("__"):
            n = f"_{n}"
        return n

    def _handle_response(self, response: dict[str, str | int | bool | list[object]]) -> MSGraphResponse:
        """Handle Microsoft Graph API response."""
        try:
            if response is None:
                    return MSGraphResponse(success=False, error="Empty response from Microsoft Graph")

            success = True
            error_msg = None

            # Handle error responses
            if hasattr(response, "error"):
                success = False
                error_msg = str(response.error) #type:ignore
            elif isinstance(response, dict) and "error" in response:
                success = False
                error_msg = str(response["error"])
            elif hasattr(response, "code") and hasattr(response, "message"):
                success = False
                error_msg = f"{response.code}: {response.message}" #type:ignore

            return MSGraphResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Microsoft OneDrive API response: {e}")
            return MSGraphResponse(success=False, error=str(e))

    def get_data_source(self) -> "OneDriveDataSource":
        """Get the underlying Microsoft OneDrive API client."""
        return self


    def drives_create_bundles(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to bundles for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/bundles (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .bundles().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_create_items(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to items for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_delete_bundles_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property bundles in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/bundles/{driveItem-id}/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .bundles().by_id(params.get("driveItem_id", "")).content()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_delete_following_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property following in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/following/{driveItem-id}/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .following().by_id(params.get("driveItem_id", "")).content()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_delete_items(self,
        *,
        drive_id: str,
        drive_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property items for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_delete_items_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property items in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).content()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_delete_list(self,
        *,
        drive_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property list for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_delete_root_content(self,
        *,
        drive_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property root in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/root/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .root().content().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_delete_special_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property special in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/special/{driveItem-id}/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .special().by_id(params.get("driveItem_id", "")).content()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_bundles(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bundles from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/bundles/{driveItem-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .bundles().by_id(params.get("driveItem_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_bundles_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property bundles from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/bundles/{driveItem-id}/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .bundles().by_id(params.get("driveItem_id", "")).content()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_created_by_user(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get createdByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/createdByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .created_by_user().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_following(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get following from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/following/{driveItem-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .following().by_id(params.get("driveItem_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_following_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property following from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/following/{driveItem-id}/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .following().by_id(params.get("driveItem_id", "")).content()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_items(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get items from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_items_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property items from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).content().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_last_modified_by_user(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get lastModifiedByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/lastModifiedByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .last_modified_by_user().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_list(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get list from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_root(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get root from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/root (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .root().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_root_content(self,
        *,
        drive_id: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property root from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/root/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .root().content().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_special(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get special from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/special/{driveItem-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .special().by_id(params.get("driveItem_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_get_special_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property special from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/special/{driveItem-id}/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .special().by_id(params.get("driveItem_id", "")).content()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_bundles(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get bundles from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/bundles (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .bundles().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_following(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get following from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/following (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .following().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """drive: sharedWithMe.

        Microsoft OneDrive API method: /drives/{drive-id}/items (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_special(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get special from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/special (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .special().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_update_bundles_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property bundles in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/bundles/{driveItem-id}/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .bundles().by_id(params.get("driveItem_id", "")).content()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_update_following_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property following in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/following/{driveItem-id}/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .following().by_id(params.get("driveItem_id", "")).content()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_update_items(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property items in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_update_items_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property items in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).content()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_update_list(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property list in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_update_root_content(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property root in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/root/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .root().content().put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_update_special_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property special in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/special/{driveItem-id}/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .special().by_id(params.get("driveItem_id", "")).content()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_bundles_get_count_c935(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/bundles/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .bundles().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_created_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/createdByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .created_by_user().mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_created_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/createdByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .created_by_user().service_provisioning_errors().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_created_by_user_service_provisioning_errors_get_count_37a0(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/createdByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .created_by_user().service_provisioning_errors().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_created_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/createdByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .created_by_user().mailbox_settings()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_create_drive(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Add new entity to drives.

        Microsoft OneDrive API method: /drives (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().drives()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_delete_drive(self,
        *,
        drive_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete entity from drives.

        Microsoft OneDrive API method: /drives/{drive-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_get_drive(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get entity from drives by key.

        Microsoft OneDrive API method: /drives/{drive-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_drive(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get entities from drives.

        Microsoft OneDrive API method: /drives (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().drives().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_update_drive(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update entity in drives.

        Microsoft OneDrive API method: /drives/{drive-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_assign_sensitivity_label(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action assignSensitivityLabel.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/assignSensitivityLabel (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).assign_sensitivity_label()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_checkin(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action checkin.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/checkin (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).checkin()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_checkout(self,
        *,
        drive_id: str,
        drive_item_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action checkout.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/checkout (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).checkout()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_copy(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action copy.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/copy (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).copy()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_create_link(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createLink.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/createLink (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).create_link()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_create_upload_session(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/createUploadSession (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_delta_9846(self,
        *,
        drive_id: str,
        drive_item_id: str,
        token: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/delta(token='{token}') (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            token (required): Usage: token='{token}'
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if token is not None:
            params["token"] = token
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).delta(token=token)()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_delta_fa14(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/delta() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_discard_checkout(self,
        *,
        drive_id: str,
        drive_item_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action discardCheckout.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/discardCheckout (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).discard_checkout()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_extract_sensitivity_labels(self,
        *,
        drive_id: str,
        drive_item_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action extractSensitivityLabels.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/extractSensitivityLabels (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).extract_sensitivity_labels()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_follow(self,
        *,
        drive_id: str,
        drive_item_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action follow.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/follow (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).follow().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_get_activities_by_interval_4c35(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function getActivitiesByInterval.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/getActivitiesByInterval() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", ""))
            .get_activities_by_interval()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_get_activities_by_interval_ad27(self,
        *,
        drive_id: str,
        drive_item_id: str,
        start_date_time: str,
        end_date_time: str,
        interval: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function getActivitiesByInterval.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}') (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            start_date_time (required): Usage: startDateTime='{startDateTime}'
            end_date_time (required): Usage: endDateTime='{endDateTime}'
            interval (required): Usage: interval='{interval}'
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if interval is not None:
            params["interval"] = interval
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", ""))
            .get_activities_by_interval(start_date_time="{start_date_time}",end_date_time="{end_date_time}",interval="{interval}")()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_invite(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action invite.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/invite (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).invite()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_permanent_delete(self,
        *,
        drive_id: str,
        drive_item_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permanentDelete (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permanent_delete()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_permissions_permission_grant(self,
        *,
        drive_id: str,
        drive_item_id: str,
        permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action grant.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permissions/{permission-id}/grant (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            permission_id (required): The unique identifier of permission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if permission_id is not None:
            params["permission-id"] = permission_id
            params["permission_id"] = permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permissions().by_id(params
            .get("permission_id", "")).grant().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_preview(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action preview.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/preview (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).preview()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_restore(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action restore.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/restore (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).restore()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_search(self,
        *,
        drive_id: str,
        drive_item_id: str,
        q: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function search.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/search(q='{q}') (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            q (required): Usage: q='{q}'
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if q is not None:
            params["q"] = q
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).search(q="{q}")()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_subscriptions_subscription_reauthorize(self,
        *,
        drive_id: str,
        drive_item_id: str,
        subscription_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action reauthorize.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/subscriptions/{subscription-id}/reauthorize (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            subscription_id (required): The unique identifier of subscription

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).subscriptions().by_id(params
            .get("subscription_id", "")).reauthorize().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_unfollow(self,
        *,
        drive_id: str,
        drive_item_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action unfollow.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/unfollow (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).unfollow()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_validate_permission(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action validatePermission.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/validatePermission (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).validate_permission()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_items_drive_item_versions_drive_item_version_restore_version(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_version_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action restoreVersion.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/{driveItemVersion-id}/restoreVersion (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_version_id (required): The unique identifier of driveItemVersion

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_version_id is not None:
            params["driveItemVersion-id"] = drive_item_version_id
            params["driveItemVersion_id"] = drive_item_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().by_id(params
            .get("driveItemVersion_id", "")).restore_version().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_add_copy(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action addCopy.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/addCopy (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().add_copy().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_add_copy_from_content_type_hub(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action addCopyFromContentTypeHub.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/addCopyFromContentTypeHub (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().add_copy_from_content_type_hub()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_content_type_associate_with_hub_sites(self,
        *,
        drive_id: str,
        content_type_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action associateWithHubSites.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/associateWithHubSites (POST).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .associate_with_hub_sites().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_content_type_copy_to_default_content_location(self,
        *,
        drive_id: str,
        content_type_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action copyToDefaultContentLocation.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/copyToDefaultContentLocation (POST).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .copy_to_default_content_location().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_content_type_is_published(self,
        *,
        drive_id: str,
        content_type_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function isPublished.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/isPublished() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .is_published()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_content_type_publish(self,
        *,
        drive_id: str,
        content_type_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action publish.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/publish (POST).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).publish()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_content_type_unpublish(self,
        *,
        drive_id: str,
        content_type_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action unpublish.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/unpublish (POST).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).unpublish()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_content_types_get_compatible_hub_content_types(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function getCompatibleHubContentTypes.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/getCompatibleHubContentTypes() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().get_compatible_hub_content_types()()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_items_delta_9846(self,
        *,
        drive_id: str,
        token: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/delta(token='{token}') (GET).

        Args:
            drive_id (required): The unique identifier of drive
            token (required): Usage: token='{token}'
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if token is not None:
            params["token"] = token
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().delta(token=token)().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_items_delta_fa14(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/delta() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_items_list_item_create_link(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createLink.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/createLink (POST).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).create_link()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_items_list_item_document_set_versions_document_set_version_restore(self,
        *,
        drive_id: str,
        list_item_id: str,
        document_set_version_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action restore.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/restore (POST).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            document_set_version_id (required): The unique identifier of documentSetVersion

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if document_set_version_id is not None:
            params["documentSetVersion-id"] = document_set_version_id
            params["documentSetVersion_id"] = document_set_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().by_id(params.get("documentSetVersion_id", ""))
            .restore().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_items_list_item_get_activities_by_interval_4c35(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function getActivitiesByInterval.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/getActivitiesByInterval() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .get_activities_by_interval()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_items_list_item_get_activities_by_interval_ad27(self,
        *,
        drive_id: str,
        list_item_id: str,
        start_date_time: str,
        end_date_time: str,
        interval: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function getActivitiesByInterval.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}') (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            start_date_time (required): Usage: startDateTime='{startDateTime}'
            end_date_time (required): Usage: endDateTime='{endDateTime}'
            interval (required): Usage: interval='{interval}'
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if interval is not None:
            params["interval"] = interval
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .get_activities_by_interval(start_date_time="{start_date_time}",end_date_time="{end_date_time}",interval="{interval}")()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_items_list_item_versions_list_item_version_restore_version(self,
        *,
        drive_id: str,
        list_item_id: str,
        list_item_version_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action restoreVersion.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/{listItemVersion-id}/restoreVersion (POST).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            list_item_version_id (required): The unique identifier of listItemVersion

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if list_item_version_id is not None:
            params["listItemVersion-id"] = list_item_version_id
            params["listItemVersion_id"] = list_item_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .by_id(params.get("listItemVersion_id", "")).restore_version()
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_list_subscriptions_subscription_reauthorize(self,
        *,
        drive_id: str,
        subscription_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action reauthorize.

        Microsoft OneDrive API method: /drives/{drive-id}/list/subscriptions/{subscription-id}/reauthorize (POST).

        Args:
            drive_id (required): The unique identifier of drive
            subscription_id (required): The unique identifier of subscription

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().subscriptions().by_id(params.get("subscription_id", ""))
            .reauthorize().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_recent(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function recent.

        Microsoft OneDrive API method: /drives/{drive-id}/recent() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .recent()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_search(self,
        *,
        drive_id: str,
        q: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function search.

        Microsoft OneDrive API method: /drives/{drive-id}/search(q='{q}') (GET).

        Args:
            drive_id (required): The unique identifier of drive
            q (required): Usage: q='{q}'
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if q is not None:
            params["q"] = q
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .search(q="{q}")().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_drive_shared_with_me(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function sharedWithMe.

        Microsoft OneDrive API method: /drives/{drive-id}/sharedWithMe() (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .shared_with_me()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_following_get_count_16f3(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/following/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .following().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_create_children(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to children for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/children (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).children()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_create_permissions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to permissions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permissions (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permissions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_create_subscriptions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to subscriptions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/subscriptions (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).subscriptions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_create_thumbnails(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to thumbnails for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/thumbnails (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).thumbnails()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_create_versions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to versions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_analytics(self,
        *,
        drive_id: str,
        drive_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property analytics for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_children_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_id1: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property children in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/children/{driveItem-id1}/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_id1 (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_id1 is not None:
            params["driveItem-id1"] = drive_item_id1
            params["driveItem_id1"] = drive_item_id1
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).children().by_id(params
            .get("driveItem_id1", "")).content().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_permissions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        permission_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property permissions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permissions/{permission-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            permission_id (required): The unique identifier of permission
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if permission_id is not None:
            params["permission-id"] = permission_id
            params["permission_id"] = permission_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permissions().by_id(params
            .get("permission_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_retention_label(self,
        *,
        drive_id: str,
        drive_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """driveItem: removeRetentionLabel.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/retentionLabel (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).retention_label()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_subscriptions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        subscription_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property subscriptions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/subscriptions/{subscription-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            subscription_id (required): The unique identifier of subscription
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).subscriptions().by_id(params
            .get("subscription_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_thumbnails(self,
        *,
        drive_id: str,
        drive_item_id: str,
        thumbnail_set_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property thumbnails for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/thumbnails/{thumbnailSet-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            thumbnail_set_id (required): The unique identifier of thumbnailSet
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if thumbnail_set_id is not None:
            params["thumbnailSet-id"] = thumbnail_set_id
            params["thumbnailSet_id"] = thumbnail_set_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).thumbnails().by_id(params
            .get("thumbnailSet_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_versions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_version_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property versions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/{driveItemVersion-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_version_id (required): The unique identifier of driveItemVersion
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_version_id is not None:
            params["driveItemVersion-id"] = drive_item_version_id
            params["driveItemVersion_id"] = drive_item_version_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().by_id(params
            .get("driveItemVersion_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_delete_versions_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_version_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property versions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/{driveItemVersion-id}/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_version_id (required): The unique identifier of driveItemVersion
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_version_id is not None:
            params["driveItemVersion-id"] = drive_item_version_id
            params["driveItemVersion_id"] = drive_item_version_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().by_id(params
            .get("driveItemVersion_id", "")).content().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_analytics(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get analytics from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_children(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_id1: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get children from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/children/{driveItem-id1} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_id1 (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_id1 is not None:
            params["driveItem-id1"] = drive_item_id1
            params["driveItem_id1"] = drive_item_id1
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).children().by_id(params
            .get("driveItem_id1", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_children_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_id1: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property children from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/children/{driveItem-id1}/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_id1 (required): The unique identifier of driveItem
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_id1 is not None:
            params["driveItem-id1"] = drive_item_id1
            params["driveItem_id1"] = drive_item_id1
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).children().by_id(params
            .get("driveItem_id1", "")).content().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_count_9c16(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_created_by_user(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get createdByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/createdByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).created_by_user()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_last_modified_by_user(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get lastModifiedByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/lastModifiedByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).last_modified_by_user()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_list_item(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get listItem from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/listItem (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).list_item()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_permissions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        permission_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get permissions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permissions/{permission-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            permission_id (required): The unique identifier of permission
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if permission_id is not None:
            params["permission-id"] = permission_id
            params["permission_id"] = permission_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permissions().by_id(params
            .get("permission_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_retention_label(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get retentionLabel from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/retentionLabel (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).retention_label()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_subscriptions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        subscription_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get subscriptions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/subscriptions/{subscription-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            subscription_id (required): The unique identifier of subscription
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).subscriptions().by_id(params
            .get("subscription_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_thumbnails(self,
        *,
        drive_id: str,
        drive_item_id: str,
        thumbnail_set_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get thumbnails from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/thumbnails/{thumbnailSet-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            thumbnail_set_id (required): The unique identifier of thumbnailSet
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if thumbnail_set_id is not None:
            params["thumbnailSet-id"] = thumbnail_set_id
            params["thumbnailSet_id"] = thumbnail_set_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).thumbnails().by_id(params
            .get("thumbnailSet_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_versions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_version_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get versions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/{driveItemVersion-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_version_id (required): The unique identifier of driveItemVersion
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_version_id is not None:
            params["driveItemVersion-id"] = drive_item_version_id
            params["driveItemVersion_id"] = drive_item_version_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().by_id(params
            .get("driveItemVersion_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_get_versions_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_version_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property versions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/{driveItemVersion-id}/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_version_id (required): The unique identifier of driveItemVersion

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_version_id is not None:
            params["driveItemVersion-id"] = drive_item_version_id
            params["driveItemVersion_id"] = drive_item_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().by_id(params
            .get("driveItemVersion_id", "")).content().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_list_children(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List children of a driveItem.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/children (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).children()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_list_permissions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get permissions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permissions (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permissions()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_list_subscriptions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get subscriptions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/subscriptions (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).subscriptions()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_list_thumbnails(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get thumbnails from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/thumbnails (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).thumbnails()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_list_versions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get versions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_analytics(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property analytics in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_children_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_id1: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property children in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/children/{driveItem-id1}/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_id1 (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_id1 is not None:
            params["driveItem-id1"] = drive_item_id1
            params["driveItem_id1"] = drive_item_id1
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).children().by_id(params
            .get("driveItem_id1", "")).content().put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_permissions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property permissions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permissions/{permission-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            permission_id (required): The unique identifier of permission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if permission_id is not None:
            params["permission-id"] = permission_id
            params["permission_id"] = permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permissions().by_id(params
            .get("permission_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_retention_label(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """driveItem: lockOrUnlockRecord.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/retentionLabel (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).retention_label()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_subscriptions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        subscription_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property subscriptions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/subscriptions/{subscription-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            subscription_id (required): The unique identifier of subscription
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).subscriptions().by_id(params
            .get("subscription_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_thumbnails(self,
        *,
        drive_id: str,
        drive_item_id: str,
        thumbnail_set_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property thumbnails in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/thumbnails/{thumbnailSet-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            thumbnail_set_id (required): The unique identifier of thumbnailSet
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if thumbnail_set_id is not None:
            params["thumbnailSet-id"] = thumbnail_set_id
            params["thumbnailSet_id"] = thumbnail_set_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).thumbnails().by_id(params
            .get("thumbnailSet_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_versions(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_version_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property versions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/{driveItemVersion-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_version_id (required): The unique identifier of driveItemVersion
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_version_id is not None:
            params["driveItemVersion-id"] = drive_item_version_id
            params["driveItemVersion_id"] = drive_item_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().by_id(params
            .get("driveItemVersion_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_update_versions_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        drive_item_version_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property versions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/{driveItemVersion-id}/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            drive_item_version_id (required): The unique identifier of driveItemVersion
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if drive_item_version_id is not None:
            params["driveItemVersion-id"] = drive_item_version_id
            params["driveItemVersion_id"] = drive_item_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().by_id(params
            .get("driveItemVersion_id", "")).content()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_create_item_activity_stats(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to itemActivityStats for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_delete_item_activity_stats(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property itemActivityStats for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_get_all_time(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get itemAnalytics.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/allTime (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics().all_time()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_get_item_activity_stats(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get itemActivityStats from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_get_last_seven_days(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get lastSevenDays from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/lastSevenDays (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .last_seven_days().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_list_item_activity_stats(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get itemActivityStats from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_update_item_activity_stats(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property itemActivityStats in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_create_activities(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to activities for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities (POST).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_delete_activities(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        item_activity_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property activities for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            item_activity_id (required): The unique identifier of itemActivity
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if item_activity_id is not None:
            params["itemActivity-id"] = item_activity_id
            params["itemActivity_id"] = item_activity_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().by_id(params.get("itemActivity_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_get_activities(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        item_activity_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get activities from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            item_activity_id (required): The unique identifier of itemActivity
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if item_activity_id is not None:
            params["itemActivity-id"] = item_activity_id
            params["itemActivity_id"] = item_activity_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().by_id(params.get("itemActivity_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_get_count_f4fa(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_list_activities(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get activities from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_update_activities(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        item_activity_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property activities in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            item_activity_id (required): The unique identifier of itemActivity
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if item_activity_id is not None:
            params["itemActivity-id"] = item_activity_id
            params["itemActivity_id"] = item_activity_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().by_id(params.get("itemActivity_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_activities_delete_drive_item_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        item_activity_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property driveItem in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            item_activity_id (required): The unique identifier of itemActivity
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if item_activity_id is not None:
            params["itemActivity-id"] = item_activity_id
            params["itemActivity_id"] = item_activity_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().by_id(params.get("itemActivity_id", "")).drive_item()
            .content().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_activities_get_count_7511(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_activities_get_drive_item(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        item_activity_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get driveItem from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            item_activity_id (required): The unique identifier of itemActivity
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if item_activity_id is not None:
            params["itemActivity-id"] = item_activity_id
            params["itemActivity_id"] = item_activity_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().by_id(params.get("itemActivity_id", "")).drive_item()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_activities_get_drive_item_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        item_activity_id: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property driveItem from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            item_activity_id (required): The unique identifier of itemActivity
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if item_activity_id is not None:
            params["itemActivity-id"] = item_activity_id
            params["itemActivity_id"] = item_activity_id
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().by_id(params.get("itemActivity_id", "")).drive_item()
            .content().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_analytics_item_activity_stats_activities_update_drive_item_content(self,
        *,
        drive_id: str,
        drive_item_id: str,
        item_activity_stat_id: str,
        item_activity_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property driveItem in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            item_activity_stat_id (required): The unique identifier of itemActivityStat
            item_activity_id (required): The unique identifier of itemActivity
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if item_activity_stat_id is not None:
            params["itemActivityStat-id"] = item_activity_stat_id
            params["itemActivityStat_id"] = item_activity_stat_id
        if item_activity_id is not None:
            params["itemActivity-id"] = item_activity_id
            params["itemActivity_id"] = item_activity_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).analytics()
            .item_activity_stats().by_id(params.get("itemActivityStat_id", ""))
            .activities().by_id(params.get("itemActivity_id", "")).drive_item()
            .content().put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_children_get_count_17b0(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/children/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).children().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_created_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/createdByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).created_by_user()
            .mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_created_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/createdByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).created_by_user()
            .service_provisioning_errors().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_created_by_user_service_provisioning_errors_get_count_9567(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/createdByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).created_by_user()
            .service_provisioning_errors().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_created_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/createdByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).created_by_user()
            .mailbox_settings().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_last_modified_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        drive_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/lastModifiedByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).last_modified_by_user()
            .mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_last_modified_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/lastModifiedByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).last_modified_by_user()
            .service_provisioning_errors().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_last_modified_by_user_service_provisioning_errors_get_count_b2d2(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/lastModifiedByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).last_modified_by_user()
            .service_provisioning_errors().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_last_modified_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        drive_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/lastModifiedByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).last_modified_by_user()
            .mailbox_settings().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_permissions_get_count_d367(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/permissions/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).permissions().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_subscriptions_get_count_f848(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/subscriptions/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).subscriptions().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_thumbnails_get_count_50f0(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/thumbnails/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).thumbnails().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_items_versions_get_count_6d7a(self,
        *,
        drive_id: str,
        drive_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/items/{driveItem-id}/versions/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            drive_item_id (required): The unique identifier of driveItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if drive_item_id is not None:
            params["driveItem-id"] = drive_item_id
            params["driveItem_id"] = drive_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .items().by_id(params.get("driveItem_id", "")).versions().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_last_modified_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/lastModifiedByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .last_modified_by_user().mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_last_modified_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/lastModifiedByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .last_modified_by_user().service_provisioning_errors().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_last_modified_by_user_service_provisioning_errors_get_count_54d5(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/lastModifiedByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .last_modified_by_user().service_provisioning_errors().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_last_modified_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/lastModifiedByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .last_modified_by_user().mailbox_settings()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_create_columns(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to columns for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/columns (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().columns().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_create_content_types(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to contentTypes for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_create_items(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to items for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_create_operations(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to operations for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/operations (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().operations().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_create_subscriptions(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to subscriptions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/subscriptions (POST).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().subscriptions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_delete_columns(self,
        *,
        drive_id: str,
        column_definition_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property columns for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/columns/{columnDefinition-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            column_definition_id (required): The unique identifier of columnDefinition
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().columns().by_id(params.get("columnDefinition_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_delete_content_types(self,
        *,
        drive_id: str,
        content_type_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property contentTypes for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_delete_items(self,
        *,
        drive_id: str,
        list_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property items for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_delete_operations(self,
        *,
        drive_id: str,
        rich_long_running_operation_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property operations for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/operations/{richLongRunningOperation-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            rich_long_running_operation_id (required): The unique identifier of richLongRunningOperation
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if rich_long_running_operation_id is not None:
            params["richLongRunningOperation-id"] = rich_long_running_operation_id
            params["richLongRunningOperation_id"] = rich_long_running_operation_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().operations().by_id(params.get("richLongRunningOperation_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_delete_subscriptions(self,
        *,
        drive_id: str,
        subscription_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property subscriptions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/subscriptions/{subscription-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            subscription_id (required): The unique identifier of subscription
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().subscriptions().by_id(params.get("subscription_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_columns(self,
        *,
        drive_id: str,
        column_definition_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columns from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/columns/{columnDefinition-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            column_definition_id (required): The unique identifier of columnDefinition
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().columns().by_id(params.get("columnDefinition_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_content_types(self,
        *,
        drive_id: str,
        content_type_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contentTypes from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_created_by_user(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get createdByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/createdByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().created_by_user().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_drive(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drive from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/drive (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().drive().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_items(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get items from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_last_modified_by_user(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get lastModifiedByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/lastModifiedByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().last_modified_by_user().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_operations(self,
        *,
        drive_id: str,
        rich_long_running_operation_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get operations from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/operations/{richLongRunningOperation-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            rich_long_running_operation_id (required): The unique identifier of richLongRunningOperation
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if rich_long_running_operation_id is not None:
            params["richLongRunningOperation-id"] = rich_long_running_operation_id
            params["richLongRunningOperation_id"] = rich_long_running_operation_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().operations().by_id(params.get("richLongRunningOperation_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_get_subscriptions(self,
        *,
        drive_id: str,
        subscription_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get subscriptions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/subscriptions/{subscription-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            subscription_id (required): The unique identifier of subscription
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().subscriptions().by_id(params.get("subscription_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_list_columns(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columns from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/columns (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().columns().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_list_content_types(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contentTypes from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_list_items(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get items from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_list_operations(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get operations from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/operations (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().operations().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_list_subscriptions(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get subscriptions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/subscriptions (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().subscriptions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_update_columns(self,
        *,
        drive_id: str,
        column_definition_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property columns in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/columns/{columnDefinition-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            column_definition_id (required): The unique identifier of columnDefinition
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().columns().by_id(params.get("columnDefinition_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_update_content_types(self,
        *,
        drive_id: str,
        content_type_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property contentTypes in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_update_items(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property items in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_update_operations(self,
        *,
        drive_id: str,
        rich_long_running_operation_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property operations in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/operations/{richLongRunningOperation-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            rich_long_running_operation_id (required): The unique identifier of richLongRunningOperation
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if rich_long_running_operation_id is not None:
            params["richLongRunningOperation-id"] = rich_long_running_operation_id
            params["richLongRunningOperation_id"] = rich_long_running_operation_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().operations().by_id(params.get("richLongRunningOperation_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_update_subscriptions(self,
        *,
        drive_id: str,
        subscription_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property subscriptions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/subscriptions/{subscription-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            subscription_id (required): The unique identifier of subscription
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if subscription_id is not None:
            params["subscription-id"] = subscription_id
            params["subscription_id"] = subscription_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().subscriptions().by_id(params.get("subscription_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_columns_get_count_5e2e(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/columns/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().columns().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_columns_get_source_column(self,
        *,
        drive_id: str,
        column_definition_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get sourceColumn from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/columns/{columnDefinition-id}/sourceColumn (GET).

        Args:
            drive_id (required): The unique identifier of drive
            column_definition_id (required): The unique identifier of columnDefinition
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().columns().by_id(params.get("columnDefinition_id", ""))
            .source_column().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_create_column_links(self,
        *,
        drive_id: str,
        content_type_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to columnLinks for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnLinks (POST).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_links().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_create_columns(self,
        *,
        drive_id: str,
        content_type_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to columns for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columns (POST).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).columns()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_delete_column_links(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_link_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property columnLinks for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnLinks/{columnLink-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_link_id (required): The unique identifier of columnLink
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_link_id is not None:
            params["columnLink-id"] = column_link_id
            params["columnLink_id"] = column_link_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_links().by_id(params.get("columnLink_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_delete_columns(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_definition_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property columns for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columns/{columnDefinition-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_definition_id (required): The unique identifier of columnDefinition
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).columns()
            .by_id(params.get("columnDefinition_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_get_base(self,
        *,
        drive_id: str,
        content_type_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get base from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/base (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).base()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_get_base_types(self,
        *,
        drive_id: str,
        content_type_id: str,
        content_type_id1: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get baseTypes from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/baseTypes/{contentType-id1} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            content_type_id1 (required): The unique identifier of contentType
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if content_type_id1 is not None:
            params["contentType-id1"] = content_type_id1
            params["contentType_id1"] = content_type_id1
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .base_types().by_id(params.get("contentType_id1", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_get_column_links(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_link_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columnLinks from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnLinks/{columnLink-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_link_id (required): The unique identifier of columnLink
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_link_id is not None:
            params["columnLink-id"] = column_link_id
            params["columnLink_id"] = column_link_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_links().by_id(params.get("columnLink_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_get_column_positions(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_definition_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columnPositions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnPositions/{columnDefinition-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_definition_id (required): The unique identifier of columnDefinition
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_positions().by_id(params.get("columnDefinition_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_get_columns(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_definition_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columns from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columns/{columnDefinition-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_definition_id (required): The unique identifier of columnDefinition
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).columns()
            .by_id(params.get("columnDefinition_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_get_count_5838(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_list_base_types(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get baseTypes from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/baseTypes (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .base_types().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_list_column_links(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columnLinks from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnLinks (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_links().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_list_column_positions(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columnPositions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnPositions (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_positions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_list_columns(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get columns from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columns (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).columns()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_update_column_links(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_link_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property columnLinks in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnLinks/{columnLink-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_link_id (required): The unique identifier of columnLink
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_link_id is not None:
            params["columnLink-id"] = column_link_id
            params["columnLink_id"] = column_link_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_links().by_id(params.get("columnLink_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_update_columns(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_definition_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property columns in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columns/{columnDefinition-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_definition_id (required): The unique identifier of columnDefinition
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).columns()
            .by_id(params.get("columnDefinition_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_base_types_get_count_95ae(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/baseTypes/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .base_types().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_column_links_get_count_e5f5(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnLinks/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_links().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_column_positions_get_count_6cb6(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columnPositions/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", ""))
            .column_positions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_columns_get_count_4b13(self,
        *,
        drive_id: str,
        content_type_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columns/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).columns()
            .count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_content_types_columns_get_source_column(self,
        *,
        drive_id: str,
        content_type_id: str,
        column_definition_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get sourceColumn from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/contentTypes/{contentType-id}/columns/{columnDefinition-id}/sourceColumn (GET).

        Args:
            drive_id (required): The unique identifier of drive
            content_type_id (required): The unique identifier of contentType
            column_definition_id (required): The unique identifier of columnDefinition
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if content_type_id is not None:
            params["contentType-id"] = content_type_id
            params["contentType_id"] = content_type_id
        if column_definition_id is not None:
            params["columnDefinition-id"] = column_definition_id
            params["columnDefinition_id"] = column_definition_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().content_types().by_id(params.get("contentType_id", "")).columns()
            .by_id(params.get("columnDefinition_id", "")).source_column()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_created_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/createdByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().created_by_user().mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_created_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/createdByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().created_by_user().service_provisioning_errors().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_created_by_user_service_provisioning_errors_get_count_63ab(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/createdByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().created_by_user().service_provisioning_errors().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_created_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/list/createdByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().created_by_user().mailbox_settings()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_create_document_set_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to documentSetVersions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions (POST).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_create_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to versions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions (POST).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_delete_document_set_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        document_set_version_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property documentSetVersions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/{documentSetVersion-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            document_set_version_id (required): The unique identifier of documentSetVersion
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if document_set_version_id is not None:
            params["documentSetVersion-id"] = document_set_version_id
            params["documentSetVersion_id"] = document_set_version_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().by_id(params.get("documentSetVersion_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_delete_drive_item_content(self,
        *,
        drive_id: str,
        list_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete content for the navigation property driveItem in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/driveItem/content (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).drive_item().content()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_delete_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property fields for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/fields (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).fields()
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_delete_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        list_item_version_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property versions for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/{listItemVersion-id} (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            list_item_version_id (required): The unique identifier of listItemVersion
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if list_item_version_id is not None:
            params["listItemVersion-id"] = list_item_version_id
            params["listItemVersion_id"] = list_item_version_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .by_id(params.get("listItemVersion_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_analytics(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get analytics from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/analytics (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).analytics()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_count_e46a(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_created_by_user(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get createdByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/createdByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).created_by_user()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_document_set_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        document_set_version_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get documentSetVersions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/{documentSetVersion-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            document_set_version_id (required): The unique identifier of documentSetVersion
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if document_set_version_id is not None:
            params["documentSetVersion-id"] = document_set_version_id
            params["documentSetVersion_id"] = document_set_version_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().by_id(params.get("documentSetVersion_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_drive_item(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get driveItem from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/driveItem (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).drive_item()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_drive_item_content(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_format: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get content for the navigation property driveItem from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/driveItem/content (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_format (optional): Format of the content

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_format is not None:
            params["$format"] = dollar_format
            params["dollar_format"] = dollar_format
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).drive_item().content()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get fields from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/fields (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).fields()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_last_modified_by_user(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get lastModifiedByUser from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/lastModifiedByUser (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .last_modified_by_user().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_get_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        list_item_version_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get versions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/{listItemVersion-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            list_item_version_id (required): The unique identifier of listItemVersion
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if list_item_version_id is not None:
            params["listItemVersion-id"] = list_item_version_id
            params["listItemVersion_id"] = list_item_version_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .by_id(params.get("listItemVersion_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_list_document_set_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get documentSetVersions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_list_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get versions from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_update_document_set_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        document_set_version_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property documentSetVersions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/{documentSetVersion-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            document_set_version_id (required): The unique identifier of documentSetVersion
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if document_set_version_id is not None:
            params["documentSetVersion-id"] = document_set_version_id
            params["documentSetVersion_id"] = document_set_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().by_id(params.get("documentSetVersion_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_update_drive_item_content(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update content for the navigation property driveItem in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/driveItem/content (PUT).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).drive_item().content()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_update_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property fields in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/fields (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).fields()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_update_versions(self,
        *,
        drive_id: str,
        list_item_id: str,
        list_item_version_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property versions in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/{listItemVersion-id} (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            list_item_version_id (required): The unique identifier of listItemVersion
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if list_item_version_id is not None:
            params["listItemVersion-id"] = list_item_version_id
            params["listItemVersion_id"] = list_item_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .by_id(params.get("listItemVersion_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_created_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/createdByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).created_by_user()
            .mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_created_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/createdByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).created_by_user()
            .service_provisioning_errors().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_created_by_user_service_provisioning_errors_get_count_5900(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/createdByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).created_by_user()
            .service_provisioning_errors().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_created_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/createdByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).created_by_user()
            .mailbox_settings().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_document_set_versions_delete_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        document_set_version_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property fields for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            document_set_version_id (required): The unique identifier of documentSetVersion
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if document_set_version_id is not None:
            params["documentSetVersion-id"] = document_set_version_id
            params["documentSetVersion_id"] = document_set_version_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().by_id(params.get("documentSetVersion_id", ""))
            .fields().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_document_set_versions_get_count_7796(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_document_set_versions_get_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        document_set_version_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get fields from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            document_set_version_id (required): The unique identifier of documentSetVersion
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if document_set_version_id is not None:
            params["documentSetVersion-id"] = document_set_version_id
            params["documentSetVersion_id"] = document_set_version_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().by_id(params.get("documentSetVersion_id", ""))
            .fields().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_document_set_versions_update_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        document_set_version_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property fields in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            document_set_version_id (required): The unique identifier of documentSetVersion
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if document_set_version_id is not None:
            params["documentSetVersion-id"] = document_set_version_id
            params["documentSetVersion_id"] = document_set_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .document_set_versions().by_id(params.get("documentSetVersion_id", ""))
            .fields().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_last_modified_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        list_item_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/lastModifiedByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .last_modified_by_user().mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_last_modified_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/lastModifiedByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .last_modified_by_user().service_provisioning_errors().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_last_modified_by_user_service_provisioning_errors_get_count_06dd(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/lastModifiedByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .last_modified_by_user().service_provisioning_errors().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_last_modified_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        list_item_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/lastModifiedByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", ""))
            .last_modified_by_user().mailbox_settings()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_versions_delete_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        list_item_version_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property fields for drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/{listItemVersion-id}/fields (DELETE).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            list_item_version_id (required): The unique identifier of listItemVersion
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if list_item_version_id is not None:
            params["listItemVersion-id"] = list_item_version_id
            params["listItemVersion_id"] = list_item_version_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .by_id(params.get("listItemVersion_id", "")).fields().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_versions_get_count_c6e1(self,
        *,
        drive_id: str,
        list_item_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_versions_get_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        list_item_version_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get fields from drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/{listItemVersion-id}/fields (GET).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            list_item_version_id (required): The unique identifier of listItemVersion
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if list_item_version_id is not None:
            params["listItemVersion-id"] = list_item_version_id
            params["listItemVersion_id"] = list_item_version_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .by_id(params.get("listItemVersion_id", "")).fields().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_items_versions_update_fields(self,
        *,
        drive_id: str,
        list_item_id: str,
        list_item_version_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property fields in drives.

        Microsoft OneDrive API method: /drives/{drive-id}/list/items/{listItem-id}/versions/{listItemVersion-id}/fields (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            list_item_id (required): The unique identifier of listItem
            list_item_version_id (required): The unique identifier of listItemVersion
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if list_item_id is not None:
            params["listItem-id"] = list_item_id
            params["listItem_id"] = list_item_id
        if list_item_version_id is not None:
            params["listItemVersion-id"] = list_item_version_id
            params["listItemVersion_id"] = list_item_version_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().items().by_id(params.get("listItem_id", "")).versions()
            .by_id(params.get("listItemVersion_id", "")).fields()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_last_modified_by_user_get_mailbox_settings(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get mailboxSettings property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/lastModifiedByUser/mailboxSettings (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().last_modified_by_user().mailbox_settings().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_last_modified_by_user_list_service_provisioning_errors(self,
        *,
        drive_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get serviceProvisioningErrors property value.

        Microsoft OneDrive API method: /drives/{drive-id}/list/lastModifiedByUser/serviceProvisioningErrors (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().last_modified_by_user().service_provisioning_errors()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_last_modified_by_user_service_provisioning_errors_get_count_d262(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/lastModifiedByUser/serviceProvisioningErrors/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().last_modified_by_user().service_provisioning_errors().count()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_last_modified_by_user_update_mailbox_settings(self,
        *,
        drive_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update property mailboxSettings value..

        Microsoft OneDrive API method: /drives/{drive-id}/list/lastModifiedByUser/mailboxSettings (PATCH).

        Args:
            drive_id (required): The unique identifier of drive
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().last_modified_by_user().mailbox_settings()
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_operations_get_count_e8e7(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/operations/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().operations().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_list_subscriptions_get_count_59f5(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/list/subscriptions/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .list().subscriptions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def drives_special_get_count_6a2e(self,
        *,
        drive_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /drives/{drive-id}/special/$count (GET).

        Args:
            drive_id (required): The unique identifier of drive
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.drives_by_id(params.get("drive_id", ""))  # type:ignore
            .special().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def groups_get_drive(self,
        *,
        group_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drive from groups.

        Microsoft OneDrive API method: /groups/{group-id}/drive (GET).

        Args:
            group_id (required): The unique identifier of group
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if group_id is not None:
            params["group-id"] = group_id
            params["group_id"] = group_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().groups().by_id(params  # type:ignore
            .get("group_id", "")).drive().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def groups_get_drives(self,
        *,
        group_id: str,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drives from groups.

        Microsoft OneDrive API method: /groups/{group-id}/drives/{drive-id} (GET).

        Args:
            group_id (required): The unique identifier of group
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if group_id is not None:
            params["group-id"] = group_id
            params["group_id"] = group_id
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().groups().by_id(params  # type:ignore
            .get("group_id", "")).drives().by_id(params.get("drive_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def groups_list_drives(self,
        *,
        group_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drives from groups.

        Microsoft OneDrive API method: /groups/{group-id}/drives (GET).

        Args:
            group_id (required): The unique identifier of group
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if group_id is not None:
            params["group-id"] = group_id
            params["group_id"] = group_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().groups().by_id(params  # type:ignore
            .get("group_id", "")).drives().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def groups_drives_get_count_9ca4(self,
        *,
        group_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /groups/{group-id}/drives/$count (GET).

        Args:
            group_id (required): The unique identifier of group
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if group_id is not None:
            params["group-id"] = group_id
            params["group_id"] = group_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().groups().by_id(params  # type:ignore
            .get("group_id", "")).drives().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def me_get_drive(self,
        *,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get Drive.

        Microsoft OneDrive API method: /me/drive (GET).

        Args:
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def me_get_drives(self,
        *,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drives from me.

        Microsoft OneDrive API method: /me/drives/{drive-id} (GET).

        Args:
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().me().drives().by_id(params  # type:ignore
            .get("drive_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def me_list_drives(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List available drives.

        Microsoft OneDrive API method: /me/drives (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().me().drives().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def me_drives_get_count_2023(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /me/drives/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().me().drives().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def users_get_drive(self,
        *,
        user_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drive from users.

        Microsoft OneDrive API method: /users/{user-id}/drive (GET).

        Args:
            user_id (required): The unique identifier of user
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().users().by_id(params  # type:ignore
            .get("user_id", "")).drive().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def users_get_drives(self,
        *,
        user_id: str,
        drive_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drives from users.

        Microsoft OneDrive API method: /users/{user-id}/drives/{drive-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            drive_id (required): The unique identifier of drive
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if drive_id is not None:
            params["drive-id"] = drive_id
            params["drive_id"] = drive_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().users().by_id(params  # type:ignore
            .get("user_id", "")).drives().by_id(params.get("drive_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def users_list_drives(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get drives from users.

        Microsoft OneDrive API method: /users/{user-id}/drives (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().users().by_id(params  # type:ignore
            .get("user_id", "")).drives().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )

    def users_drives_get_count_7cd7(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft OneDrive API method: /users/{user-id}/drives/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().drive().users().by_id(params  # type:ignore
            .get("user_id", "")).drives().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft OneDrive API API call failed: {e!s}",
            )
