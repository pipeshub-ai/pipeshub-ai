    # Auto-generated Microsoft Contacts API client.
    # ruff: noqa: BLE001, D417, E501, ANN003, PGH003, PLR0912, C901, PLR0913, G004, TRY400, TRY003, EM101, D100, INP001, PLR0915, D401

import keyword as _kw
import logging
import re
from collections.abc import Mapping

from app.sources.client.microsoft.microsoft import MSGraphClient, MSGraphResponse

# Set up logger
logger = logging.getLogger(__name__)

class ContactsDataSource:
    """Auto-generated Microsoft Contacts API client wrapper.

    - Uses Microsoft Graph SDK client internally
    - Snake_case method names for all Microsoft Contacts API operations
    - Standardized MSGraphResponse format for all responses
    - No direct HTTP calls - all requests go through Graph SDK
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client."""
        self.client = client.get_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Microsoft Contacts API client initialized successfully")

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
            logger.error(f"Error handling Microsoft Contacts API response: {e}")
            return MSGraphResponse(success=False, error=str(e))

    def get_data_source(self) -> "ContactsDataSource":
        """Get the underlying Microsoft Contacts API client."""
        return self


    def me_create_contact_folders(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create ContactFolder.

        Microsoft Contacts API method: /me/contactFolders (POST).

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
            self.client.me().contacts().contact_folders()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_create_contacts(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create contact.

        Microsoft Contacts API method: /me/contacts (POST).

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
            self.client.me().contacts().contacts()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_delete_contact_folders(self,
        *,
        contact_folder_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete contactFolder.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id} (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_delete_contacts(self,
        *,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete contact.

        Microsoft Contacts API method: /me/contacts/{contact-id} (DELETE).

        Args:
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_get_contact_folders(self,
        *,
        contact_folder_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contactFolder.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id} (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_get_contacts(self,
        *,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contact.

        Microsoft Contacts API method: /me/contacts/{contact-id} (GET).

        Args:
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_list_contact_folders(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List contactFolders.

        Microsoft Contacts API method: /me/contactFolders (GET).

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
            self.client.me().contacts().contact_folders()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_list_contacts(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List contacts.

        Microsoft Contacts API method: /me/contacts (GET).

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
            self.client.me().contacts().contacts().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_update_contact_folders(self,
        *,
        contact_folder_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update contactfolder.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id} (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_update_contacts(self,
        *,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update contact.

        Microsoft Contacts API method: /me/contacts/{contact-id} (PATCH).

        Args:
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_create_child_folders(self,
        *,
        contact_folder_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create ContactFolder.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_create_contacts(self,
        *,
        contact_folder_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create contact.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_delete_child_folders(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property childFolders for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1} (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_delete_contacts(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property contacts for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id} (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_get_child_folders(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get childFolders from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1} (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_get_contacts(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id} (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_get_count_35c1(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contactFolders/$count (GET).

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
            self.client.me().contacts().contact_folders().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_list_child_folders(self,
        *,
        contact_folder_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List childFolders.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_list_contacts(self,
        *,
        contact_folder_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List contacts.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_update_child_folders(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property childFolders in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1} (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_update_contacts(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property contacts in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id} (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_create_contacts(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to contacts for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_delete_contacts(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property contacts for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id} (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_get_contacts(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id} (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_get_count_bbef(self,
        *,
        contact_folder_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/$count (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_list_contacts(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_update_contacts(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property contacts in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id} (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_create_extensions(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_delete_extensions(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/{extension-id} (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_delete_photo_content(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete media content for the navigation property photo in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo/$value (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_get_count_bf12(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/$count (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_get_extensions(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/{extension-id} (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_get_photo(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get photo from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_get_photo_content(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Get media content for the navigation property photo from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo/$value (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_list_extensions(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_update_extensions(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/{extension-id} (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_update_photo(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property photo in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_update_photo_content(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update media content for the navigation property photo in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo/$value (PUT).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_child_folders_contacts_extensions_get_count_0ca5(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/$count (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contact_folder_child_folders_contact_folder_contacts_contact_permanent_delete(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/permanentDelete (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contact_folder_child_folders_contact_folder_contacts_delta(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/delta() (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contact_folder_child_folders_contact_folder_permanent_delete(self,
        *,
        contact_folder_id: str,
        contact_folder_id1: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/permanentDelete (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contact_folder_child_folders_delta(self,
        *,
        contact_folder_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/childFolders/delta() (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contact_folder_contacts_contact_permanent_delete(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/permanentDelete (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contact_folder_contacts_delta(self,
        *,
        contact_folder_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/delta() (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contact_folder_permanent_delete(self, *, contact_folder_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/permanentDelete (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_create_extensions(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions (POST).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_delete_extensions(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/{extension-id} (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_delete_photo_content(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete media content for the navigation property photo in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo/$value (DELETE).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_get_count_31d1(self,
        *,
        contact_folder_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/$count (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_get_extensions(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/{extension-id} (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_get_photo(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get photo from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_get_photo_content(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Get media content for the navigation property photo from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo/$value (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_list_extensions(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_update_extensions(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/{extension-id} (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_update_photo(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property photo in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo (PATCH).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_update_photo_content(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update media content for the navigation property photo in me.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo/$value (PUT).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_contacts_extensions_get_count_d89c(self,
        *,
        contact_folder_id: str,
        contact_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/$count (GET).

        Args:
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contact_folders().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contact_folders_delta(self,
        *,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /me/contactFolders/delta() (GET).

        Args:
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
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
            self.client.me().contacts().contact_folders().delta()()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_create_extensions(self,
        *,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/extensions (POST).

        Args:
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_delete_extensions(self,
        *,
        contact_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/extensions/{extension-id} (DELETE).

        Args:
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_delete_photo_content(self,
        *,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete media content for the navigation property photo in me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/photo/$value (DELETE).

        Args:
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().value().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_get_count_9c39(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contacts/$count (GET).

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
            self.client.me().contacts().contacts().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_get_extensions(self,
        *,
        contact_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/extensions/{extension-id} (GET).

        Args:
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_get_photo(self,
        *,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get photo from me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/photo (GET).

        Args:
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_get_photo_content(self, *, contact_id: str, **kwargs) -> MSGraphResponse:
        """Get media content for the navigation property photo from me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/photo/$value (GET).

        Args:
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().value().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_list_extensions(self,
        *,
        contact_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/extensions (GET).

        Args:
            contact_id (required): The unique identifier of contact
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_update_extensions(self,
        *,
        contact_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/extensions/{extension-id} (PATCH).

        Args:
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_update_photo(self,
        *,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property photo in me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/photo (PATCH).

        Args:
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_update_photo_content(self,
        *,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update media content for the navigation property photo in me.

        Microsoft Contacts API method: /me/contacts/{contact-id}/photo/$value (PUT).

        Args:
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().value()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_contact_permanent_delete(self, *, contact_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /me/contacts/{contact-id}/permanentDelete (POST).

        Args:
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_delta(self,
        *,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /me/contacts/delta() (GET).

        Args:
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
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
            self.client.me().contacts().contacts().delta()()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def me_contacts_extensions_get_count_10dd(self,
        *,
        contact_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /me/contacts/{contact-id}/extensions/$count (GET).

        Args:
            contact_id (required): The unique identifier of contact
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_create_contact_folders(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to contactFolders for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_create_contacts(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to contacts for users.

        Microsoft Contacts API method: /users/{user-id}/contacts (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_delete_contact_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property contactFolders for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_delete_contacts(self,
        *,
        user_id: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property contacts for users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_get_contact_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contactFolders from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_get_contacts(self,
        *,
        user_id: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_list_contact_folders(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contactFolders from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders (GET).

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
            self.client.me().contacts().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_list_contacts(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from users.

        Microsoft Contacts API method: /users/{user-id}/contacts (GET).

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
            self.client.me().contacts().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_update_contact_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property contactFolders in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_update_contacts(self,
        *,
        user_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property contacts in users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_create_child_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to childFolders for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_create_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to contacts for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_delete_child_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property childFolders for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_delete_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property contacts for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_get_child_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get childFolders from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_get_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_get_count_72bb(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/$count (GET).

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
            self.client.me().contacts().count().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_list_child_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get childFolders from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
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
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_list_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
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
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_update_child_folders(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property childFolders in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_update_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property contacts in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_create_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to contacts for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_delete_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property contacts for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_get_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_get_count_9149(self,
        *,
        user_id: str,
        contact_folder_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_list_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get contacts from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
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
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_update_contacts(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property contacts in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_create_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_delete_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/{extension-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_delete_photo_content(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete media content for the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo/$value (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_get_count_6cbe(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_get_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/{extension-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_get_photo(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get photo from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_get_photo_content(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Get media content for the navigation property photo from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo/$value (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_list_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
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
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_update_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/{extension-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_update_photo(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_update_photo_content(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update media content for the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/photo/$value (PUT).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_child_folders_contacts_extensions_get_count_5b30(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/extensions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_create_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_delete_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/{extension-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_delete_photo_content(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete media content for the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo/$value (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_get_count_5cc4(self,
        *,
        user_id: str,
        contact_folder_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_get_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/{extension-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_get_photo(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get photo from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_get_photo_content(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Get media content for the navigation property photo from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo/$value (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_list_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
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
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_update_extensions(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/{extension-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_update_photo(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_update_photo_content(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update media content for the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/photo/$value (PUT).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).photo().value()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contact_folders_contacts_extensions_get_count_8e14(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/extensions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_create_extensions(self,
        *,
        user_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/extensions (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_delete_extensions(self,
        *,
        user_id: str,
        contact_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/extensions/{extension-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_delete_photo_content(self,
        *,
        user_id: str,
        contact_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete media content for the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/photo/$value (DELETE).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().value().delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_get_count_4943(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contacts/$count (GET).

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
            self.client.me().contacts().count().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_get_extensions(self,
        *,
        user_id: str,
        contact_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/extensions/{extension-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_get_photo(self,
        *,
        user_id: str,
        contact_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get photo from users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/photo (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_get_photo_content(self,
        *,
        user_id: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Get media content for the navigation property photo from users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/photo/$value (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().value().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_list_extensions(self,
        *,
        user_id: str,
        contact_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/extensions (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
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
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_update_extensions(self,
        *,
        user_id: str,
        contact_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/extensions/{extension-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_update_photo(self,
        *,
        user_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/photo (PATCH).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_update_photo_content(self,
        *,
        user_id: str,
        contact_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update media content for the navigation property photo in users.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/photo/$value (PUT).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).photo().value()
            .put(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_contacts_extensions_get_count_e5d6(self,
        *,
        user_id: str,
        contact_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/extensions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_contact_folder_child_folders_contact_folder_contacts_contact_permanent_delete(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/{contact-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().by_id(params
            .get("contact_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_contact_folder_child_folders_contact_folder_contacts_delta(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/contacts/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).contacts().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_contact_folder_child_folders_contact_folder_permanent_delete(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_folder_id1: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/{contactFolder-id1}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_folder_id1 (required): The unique identifier of contactFolder

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_folder_id1 is not None:
            params["contactFolder-id1"] = contact_folder_id1
            params["contactFolder_id1"] = contact_folder_id1
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().by_id(params
            .get("contactFolder_id1", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_contact_folder_child_folders_delta(self,
        *,
        user_id: str,
        contact_folder_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/childFolders/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).child_folders().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_contact_folder_contacts_contact_permanent_delete(self,
        *,
        user_id: str,
        contact_folder_id: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/{contact-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().by_id(params
            .get("contact_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_contact_folder_contacts_delta(self,
        *,
        user_id: str,
        contact_folder_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/contacts/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
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
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).contacts().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_contact_folder_permanent_delete(self,
        *,
        user_id: str,
        contact_folder_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/{contactFolder-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_folder_id (required): The unique identifier of contactFolder

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_folder_id is not None:
            params["contactFolder-id"] = contact_folder_id
            params["contactFolder_id"] = contact_folder_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contactFolder_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contact_folders_delta(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /users/{user-id}/contactFolders/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
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
            self.client.me().contacts().delta()().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contacts_contact_permanent_delete(self,
        *,
        user_id: str,
        contact_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Contacts API method: /users/{user-id}/contacts/{contact-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            contact_id (required): The unique identifier of contact

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if contact_id is not None:
            params["contact-id"] = contact_id
            params["contact_id"] = contact_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().contacts().by_id(params  # type:ignore
            .get("contact_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )

    def users_user_contacts_delta(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Contacts API method: /users/{user-id}/contacts/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
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
            self.client.me().contacts().delta()().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Contacts API API call failed: {e!s}",
            )
