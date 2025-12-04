import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from xml.sax.saxutils import escape

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.nextcloud.nextcloud import NextcloudClient


class NextcloudDataSource:
    def __init__(self, client: NextcloudClient) -> None:
        """
        Initialize the Nextcloud Datasource.
        Ref 'Authentication':
        Authentication is handled by the client (Basic Auth or Bearer Token).
        The Datasource ensures the mandatory 'OCS-APIRequest: true' header is sent.
        """
        self._client = client.get_client()
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        try:
            # Normalize base URL (remove trailing slash)
            self.base_url = self._client.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'NextcloudDataSource':
        return self

    # User MetaData
    async def get_user_details(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get metadata for a specific user (email, quota, display name).
        Ref:
        API: GET /ocs/v1.php/cloud/users/{USERID}
        """
        # Note: OCS URLs require the ID in the path
        rel_path = f'/ocs/v1.php/cloud/users/{user_id}'
        return await self._ocs_request('GET', rel_path, headers=headers)

    async def get_users(
        self,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get a list of all user IDs. Only admin users can query the list.
        Ref:
        API: GET /ocs/v1.php/cloud/users
        """
        params = {}
        if search:
            params['search'] = search
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset

        return await self._ocs_request('GET', '/ocs/v1.php/cloud/users', params=params, headers=headers)

    # Capabilities
    async def get_capabilities(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Obtain capabilities provided by the Nextcloud server and its apps.
        Returns version, enabled apps, and file size limits.
        Ref:
        API: GET /ocs/v1.php/cloud/capabilities
        """
        return await self._ocs_request('GET', '/ocs/v1.php/cloud/capabilities', headers=headers)

    # Direct Download
    async def create_direct_download_token(
        self,
        file_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Request a unique public link to a single file (valid for 8 hours).
        Ref:
        API: POST /ocs/v2.php/apps/dav/api/v1/direct
        """
        # The docs specify fileId must be in the body
        body = {'fileId': str(file_id)}

        # We must use POST for this endpoint
        return await self._ocs_request(
            'POST',
            '/ocs/v2.php/apps/dav/api/v1/direct',
            body=body,
            headers=headers
        )

    # Auto-complete
    async def autocomplete_users(
        self,
        search: str,
        item_type: str = '',
        item_id: str = '',
        share_types: Optional[List[str]] = None,
        limit: int = 10,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Search for users using the auto-complete API.
        Ref:
        API: GET /ocs/v2.php/core/autocomplete/get
        """
        params = {
            'search': search,
            'itemType': item_type,
            'itemId': item_id,
            'limit': limit
        }

        # Handling the array parameter shareTypes[] mentioned in docs
        # Note: formatting may vary based on how the client serializes lists
        if share_types:
            params['shareTypes[]'] = share_types

        return await self._ocs_request('GET', '/ocs/v2.php/core/autocomplete/get', params=params, headers=headers)

    # Shares
    async def get_shares(
        self,
        path: Optional[str] = None,
        reshares: bool = False,
        subfiles: bool = False,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get all shares from the user, or shares for a specific file/folder.
        Ref: GET .../shares
        """
        params = {}
        if path:
            params['path'] = path
            # reshares and subfiles are only valid if path is provided
            if reshares:
                params['reshares'] = 'true'
            if subfiles:
                params['subfiles'] = 'true'

        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/files_sharing/api/v1/shares',
            params=params,
            headers=headers
        )

    async def get_share_info(
        self,
        share_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get information about a specific known Share ID.
        Ref: GET .../shares/{share_id}
        """
        return await self._ocs_request(
            'GET',
            f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}',
            headers=headers
        )

    async def create_share(
        self,
        path: str,
        share_type: int,
        share_with: Optional[str] = None,
        public_upload: Optional[bool] = None,
        password: Optional[str] = None,
        permissions: Optional[int] = None,
        expire_date: Optional[str] = None,
        note: Optional[str] = None,
        attributes: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Create a new Share.
        Args:
            path: Path to file/folder
            share_type: 0=user, 1=group, 3=public link, 4=email, 6=federated, etc.
            share_with: User/group ID, email, etc. (Mandatory for share_type 0 or 1)
            attributes: URI-encoded serialized JSON string (e.g. for preventing downloads)
        Ref: POST .../shares
        """
        body = {
            'path': path,
            'shareType': str(share_type)
        }

        if share_with:
            body['shareWith'] = share_with
        if public_upload is not None:
            body['publicUpload'] = 'true' if public_upload else 'false'
        if password:
            body['password'] = password
        if permissions is not None:
            body['permissions'] = str(permissions)
        if expire_date:
            body['expireDate'] = expire_date
        if note:
            body['note'] = note
        if attributes:
            body['attributes'] = attributes

        return await self._ocs_request(
            'POST',
            '/ocs/v2.php/apps/files_sharing/api/v1/shares',
            body=body,
            headers=headers
        )

    async def delete_share(
        self,
        share_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Remove the given share.
        Ref: DELETE .../shares/{share_id}
        """
        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}',
            headers=headers
        )

    async def update_share(
        self,
        share_id: int,
        permissions: Optional[int] = None,
        password: Optional[str] = None,
        public_upload: Optional[bool] = None,
        expire_date: Optional[str] = None,
        note: Optional[str] = None,
        attributes: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Update a given share.
        IMPORTANT: The API documentation states "Only one value can be updated per request."
        Calling this with multiple arguments set will result in API error 400.
        Ref: PUT .../shares/{share_id}
        """
        body = {}

        # Check if multiple parameters are provided
        provided_args = [
            permissions, password, public_upload,
            expire_date, note, attributes
        ]
        set_args_count = sum(1 for arg in provided_args if arg is not None)

        if set_args_count > 1:
            raise ValueError("Nextcloud API restriction: Only one share attribute can be updated per request.")

        if permissions is not None:
            body['permissions'] = str(permissions)
        elif password is not None:
            body['password'] = password
        elif public_upload is not None:
            body['publicUpload'] = 'true' if public_upload else 'false'
        elif expire_date is not None:
            body['expireDate'] = expire_date
        elif note is not None:
            body['note'] = note
        elif attributes is not None:
            body['attributes'] = attributes
        else:
             # If nothing is passed, we technically shouldn't make the call,
             # but we'll let the API handle the empty body or return early.
            raise ValueError("No update parameters provided")

        return await self._ocs_request(
            'PUT',
            f'/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}',
            body=body,
            headers=headers
        )

    # Federated Cloud Shares
    async def get_remote_shares(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get all federated cloud shares the user has ACCEPTED.
        Ref: GET .../remote_shares
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/files_sharing/api/v1/remote_shares',
            headers=headers
        )

    async def get_remote_share_info(
        self,
        share_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get information about a known Federated Cloud Share.
        Ref: GET .../remote_shares/{share_id}
        """
        return await self._ocs_request(
            'GET',
            f'/ocs/v2.php/apps/files_sharing/api/v1/remote_shares/{share_id}',
            headers=headers
        )

    async def delete_remote_share(
        self,
        share_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Locally delete a received federated cloud share (that was previously accepted).
        Ref: DELETE .../remote_shares/{share_id}
        """
        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/apps/files_sharing/api/v1/remote_shares/{share_id}',
            headers=headers
        )

    async def get_pending_remote_shares(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get all PENDING federated cloud shares (not yet accepted).
        Ref: GET .../remote_shares/pending
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/files_sharing/api/v1/remote_shares/pending',
            headers=headers
        )

    async def accept_remote_share(
        self,
        share_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Locally ACCEPT a received federated cloud share.
        Ref: POST .../remote_shares/pending/{share_id}
        """
        return await self._ocs_request(
            'POST',
            f'/ocs/v2.php/apps/files_sharing/api/v1/remote_shares/pending/{share_id}',
            headers=headers
        )

    async def decline_remote_share(
        self,
        share_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Locally DECLINE a received federated cloud share.
        Ref: DELETE .../remote_shares/pending/{share_id}
        """
        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/apps/files_sharing/api/v1/remote_shares/pending/{share_id}',
            headers=headers
        )

    async def get_inherited_shares(
        self,
        path: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get shares inherited from parent folders.
        Ref: GET /ocs/v2.php/apps/files_sharing/api/v1/shares/inherited
        """
        params = {}
        if path:
            params['path'] = path

        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/files_sharing/api/v1/shares/inherited',
            params=params,
            headers=headers
        )

    async def get_pending_shares(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get local shares that are pending acceptance by the user.
        (Distinct from federated/remote pending shares).
        Ref: GET /ocs/v2.php/apps/files_sharing/api/v1/shares/pending
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/files_sharing/api/v1/shares/pending',
            headers=headers
        )

    # Sharees
    async def search_sharees(
        self,
        search: str,
        lookup: bool = False,
        per_page: Optional[int] = None,
        item_type: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get all sharees matching a search term.
        Ref: GET .../sharees
        """
        params = {'search': search}

        if lookup:
            params['lookup'] = 'true'
        if per_page is not None:
            params['perPage'] = str(per_page)
        if item_type:
            params['itemType'] = item_type

        return await self._ocs_request(
            'GET',
            '/ocs/v1.php/apps/files_sharing/api/v1/sharees',
            params=params,
            headers=headers
        )

    async def get_recommended_sharees(
        self,
        item_type: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get sharees the sharer might want to share with.
        Ref: GET .../sharees_recommended
        """
        params = {}
        if item_type:
            params['itemType'] = item_type

        return await self._ocs_request(
            'GET',
            '/ocs/v1.php/apps/files_sharing/api/v1/sharees_recommended',
            params=params,
            headers=headers
        )

    # User Status
    async def get_current_user_status(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch the authenticated user's own status.
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/user_status/api/v1/user_status',
            headers=headers
        )

    async def set_user_status_type(
        self,
        status_type: Literal["online", "away", "dnd", "invisible", "offline"],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Set the authenticated user's status type.
        Args:
            status_type: One of 'online', 'away', 'dnd', 'invisible', 'offline'
        """
        return await self._ocs_request(
            'PUT',
            '/ocs/v2.php/apps/user_status/api/v1/user_status/status',
            body={'statusType': status_type},
            headers=headers
        )

    async def set_predefined_status_message(
        self,
        message_id: str,
        clear_at: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Set a custom message using a predefined message ID.
        Args:
            message_id: The ID of the predefined message.
            clear_at: Unix Timestamp to clear the status (optional).
        """
        body = {'messageId': message_id}
        if clear_at is not None:
            body['clearAt'] = str(clear_at)

        return await self._ocs_request(
            'PUT',
            '/ocs/v2.php/apps/user_status/api/v1/user_status/message/predefined',
            body=body,
            headers=headers
        )

    async def set_custom_status_message(
        self,
        message: str,
        status_icon: Optional[str] = None,
        clear_at: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Set a user-defined custom message.
        Args:
            message: The custom message text.
            status_icon: An emoji character (optional, max 1 char).
            clear_at: Unix Timestamp to clear the status (optional).
        """
        body = {'message': message}
        if status_icon:
            body['statusIcon'] = status_icon
        if clear_at is not None:
            body['clearAt'] = str(clear_at)

        return await self._ocs_request(
            'PUT',
            '/ocs/v2.php/apps/user_status/api/v1/user_status/message/custom',
            body=body,
            headers=headers
        )

    async def clear_status_message(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Clear the current status message.
        """
        return await self._ocs_request(
            'DELETE',
            '/ocs/v2.php/apps/user_status/api/v1/user_status/message',
            headers=headers
        )

    async def revert_status_message(
        self,
        message_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Restore a backup status (e.g., reverting after a call ends).
        Ref: DELETE .../user_status/revert/{messageId}
        """
        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/apps/user_status/api/v1/user_status/revert/{message_id}',
            headers=headers
        )

    # Predefined Statuses
    async def get_predefined_statuses(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch the list of predefined statuses available on the server.
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/user_status/api/v1/predefined_statuses',
            headers=headers
        )

    # User Statuses
    async def get_all_user_statuses(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch a list of all set user-statuses.
        """
        params = {}
        if limit is not None:
            params['limit'] = str(limit)
        if offset is not None:
            params['offset'] = str(offset)

        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/apps/user_status/api/v1/statuses',
            params=params,
            headers=headers
        )

    async def get_user_status(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch a specific user's status.
        """
        return await self._ocs_request(
            'GET',
            f'/ocs/v2.php/apps/user_status/api/v1/statuses/{user_id}',
            headers=headers
        )

    async def get_user_backup_status(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch a user's backup status (original status before an automated overwrite).
        Note: The API expects an underscore prefix, but we add it here automatically.
        """
        # The docs say: "userId can be prefixed with an _ underscore"
        # We handle that logic here so the caller just passes the normal ID.
        target_id = f"_{user_id}"

        return await self._ocs_request(
            'GET',
            f'/ocs/v2.php/apps/user_status/api/v1/statuses/{target_id}',
            headers=headers
        )

    # User Preferences
    async def set_user_preference(
        self,
        app_id: str,
        config_key: str,
        config_value: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Set a single preference for the user.
        Ref: POST .../{appId}/{configKey}
        """
        return await self._ocs_request(
            'POST',
            f'/ocs/v2.php/apps/provisioning_api/api/v1/config/users/{app_id}/{config_key}',
            body={'configValue': config_value},
            headers=headers
        )

    async def set_multiple_user_preferences(
        self,
        app_id: str,
        preferences: Dict[str, str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Set multiple preferences for the user at once.
        Args:
            app_id: The ID of the application (e.g., 'core', 'files').
            preferences: A dictionary of key-value pairs to set.
                         Example: {'lang': 'en', 'timezone': 'UTC'}
        Ref: POST .../{appId}
        """
        # The API expects 'config' as an array of key-value pairs.
        # In PHP form-encoding, this typically translates to config[key]=value.
        # We construct the body to ensure correct serialization.
        body = {}
        for key, value in preferences.items():
            body[f'config[{key}]'] = value

        return await self._ocs_request(
            'POST',
            f'/ocs/v2.php/apps/provisioning_api/api/v1/config/users/{app_id}',
            body=body,
            headers=headers
        )

    async def delete_user_preference(
        self,
        app_id: str,
        config_key: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Delete a single preference.
        Ref: DELETE .../{appId}/{configKey}
        """
        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/apps/provisioning_api/api/v1/config/users/{app_id}/{config_key}',
            headers=headers
        )

    async def delete_multiple_user_preferences(
        self,
        app_id: str,
        config_keys: List[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Delete multiple preferences at once.
        Args:
            app_id: The ID of the application.
            config_keys: A list of preference keys to delete.
        Ref: DELETE .../{appId}
        """
        # The API expects 'configKeys' as a list.
        # We manually key it as 'configKeys[]' to ensure PHP treats it as an array.
        body = {}
        if config_keys:
            body['configKeys[]'] = config_keys

        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/apps/provisioning_api/api/v1/config/users/{app_id}',
            body=body,
            headers=headers
        )

    # Translation
    async def get_translation_languages(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get available translation options and supported languages.
        Ref: GET /ocs/v2.php/translation/languages
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/translation/languages',
            headers=headers
        )

    async def translate_text(
        self,
        text: str,
        to_language: str,
        from_language: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Translate a string from one language to another.
        Args:
            text: The text to be translated.
            to_language: The ISO code of the target language.
            from_language: The ISO code of the source language.
                           If None, the server will attempt to detect the language.
        Ref: POST /ocs/v2.php/translation/translate
        """
        body = {
            'text': text,
            'toLanguage': to_language
        }

        if from_language:
            body['fromLanguage'] = from_language

        return await self._ocs_request(
            'POST',
            '/ocs/v2.php/translation/translate',
            body=body,
            headers=headers
        )

    # Text Processing
    async def get_text_processing_task_types(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get available text processing task types (e.g., for LLM prompting).
        New in version 27.1.0.
        Ref: GET /ocs/v2.php/textprocessing/tasktypes
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/textprocessing/tasktypes',
            headers=headers
        )

    async def schedule_text_processing_task(
        self,
        input_text: str,
        task_type_id: str,
        app_id: str,
        identifier: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Schedule a text processing task (e.g., prompt an LLM).
        New in version 28.
        Args:
            input_text: The input text for the task.
            task_type_id: The ID of the task type (from get_text_processing_task_types).
            app_id: The ID of the requesting app.
            identifier: An app-defined identifier for tracking the task.
        Ref: POST /ocs/v2.php/textprocessing/schedule
        """
        body = {
            'input': input_text,
            'type': task_type_id,
            'appId': app_id,
            'identifier': identifier
        }

        return await self._ocs_request(
            'POST',
            '/ocs/v2.php/textprocessing/schedule',
            body=body,
            headers=headers
        )

    async def get_text_processing_task(
        self,
        task_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch a specific text processing task by its ID.
        New in version 28.
        Note: The documentation specifies using POST for this endpoint.
        Ref: POST /ocs/v2.php/textprocessing/task/{id}
        """
        return await self._ocs_request(
            'POST',
            f'/ocs/v2.php/textprocessing/task/{task_id}',
            headers=headers
        )

    # Text2Image
    async def get_text2image_availability(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Check if any Text-To-Image providers are installed.
        Ref: GET /ocs/v2.php/text2image/is_available
        """
        return await self._ocs_request(
            'GET',
            '/ocs/v2.php/text2image/is_available',
            headers=headers
        )

    async def schedule_text2image_task(
        self,
        input_text: str,
        app_id: str,
        number_of_images: int = 8,
        identifier: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Schedule an image generation task.
        Args:
            input_text: The prompt for the image.
            app_id: The ID of the requesting app.
            number_of_images: Number of images to generate (default 8).
            identifier: App-defined identifier (optional).
        Ref: POST /ocs/v2.php/text2image/schedule
        """
        body = {
            'input': input_text,
            'appId': app_id,
            'numberOfImages': str(number_of_images)
        }
        if identifier:
            body['identifier'] = identifier

        return await self._ocs_request(
            'POST',
            '/ocs/v2.php/text2image/schedule',
            body=body,
            headers=headers
        )

    async def get_text2image_task(
        self,
        task_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch a task by ID.
        Ref: POST /ocs/v2.php/text2image/task/{id}
        """
        return await self._ocs_request(
            'POST',
            f'/ocs/v2.php/text2image/task/{task_id}',
            headers=headers
        )

    async def get_text2image_result(
        self,
        task_id: int,
        index: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch a result image (raw binary data).
        Ref: POST /ocs/v2.php/text2image/task/{id}/image/{index}
        """
        # Note: The response will be binary data.
        # The _ocs_request helper adds 'format=json', which might be ignored by the server
        # for binary responses, or might wrap the binary.
        # Based on the method signature returning HTTPResponse, the caller handles the body bytes.
        return await self._ocs_request(
            'POST',
            f'/ocs/v2.php/text2image/task/{task_id}/image/{index}',
            headers=headers
        )

    async def delete_text2image_task(
        self,
        task_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Delete a task.
        Ref: DELETE /ocs/v2.php/text2image/task/{id}
        """
        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/text2image/task/{task_id}',
            headers=headers
        )

    async def list_text2image_tasks_by_app(
        self,
        app_id: str,
        identifier: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        List tasks by App.
        Ref: DELETE /ocs/v2.php/text2image/tasks/app/{appId}
        WARNING: The documentation specifies the method as DELETE for this list operation.
        This is implemented as specified, though unusual for a 'List' operation.
        """
        body = {}
        if identifier:
            body['identifier'] = identifier

        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/text2image/tasks/app/{app_id}',
            body=body,
            headers=headers
        )

    # Out of Office
    async def get_out_of_office_data(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Fetch the ongoing or next upcoming out-of-office data for a user.
        Ref: GET /ocs/v2.php/apps/dav/api/v1/outOfOffice/{userId}
        """
        return await self._ocs_request(
            'GET',
            f'/ocs/v2.php/apps/dav/api/v1/outOfOffice/{user_id}',
            headers=headers
        )

    async def set_out_of_office_data(
        self,
        user_id: str,
        first_day: str,
        last_day: str,
        status: str,
        message: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Modify/Set out-of-office data for the user.
        Note: It is only possible to modify data for the currently logged-in user.
        Args:
            user_id: The ID of the user.
            first_day: Start date in format 'YYYY-MM-DD'.
            last_day: End date in format 'YYYY-MM-DD'.
            status: Short status text.
            message: Longer multiline message.
        Ref: POST /ocs/v2.php/apps/dav/api/v1/outOfOffice/{userId}
        """
        body = {
            'firstDay': first_day,
            'lastDay': last_day,
            'status': status,
            'message': message
        }

        return await self._ocs_request(
            'POST',
            f'/ocs/v2.php/apps/dav/api/v1/outOfOffice/{user_id}',
            body=body,
            headers=headers
        )

    async def delete_out_of_office_data(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Clear data and disable out-of-office.
        Note: It is only possible to clear data for the currently logged-in user.
        Ref: DELETE /ocs/v2.php/apps/dav/api/v1/outOfOffice/{userId}
        """
        return await self._ocs_request(
            'DELETE',
            f'/ocs/v2.php/apps/dav/api/v1/outOfOffice/{user_id}',
            headers=headers
        )

    # Remote Wipe
    async def check_remote_wipe_status(
        self,
        token: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Check if the specific client token is flagged for remote wipe.
        Typically called by a client after receiving a 401 or 403 response.
        Args:
            token: The specific App Token (or device token) to check.
        Ref: POST /index.php/core/wipe/check
        """
        return await self._ocs_request(
            'POST',
            '/index.php/core/wipe/check',
            body={'token': token},
            headers=headers
        )

    async def signal_remote_wipe_success(
        self,
        token: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Signal the server that the local data wipe is complete.
        This triggers the final cleanup on the server side (e.g. revoking the token).
        Args:
            token: The App Token (or device token) that was wiped.
        Ref: POST /index.php/core/wipe/success
        """
        return await self._ocs_request(
            'POST',
            '/index.php/core/wipe/success',
            body={'token': token},
            headers=headers
        )

    # Internal OCS API helpers
    def _as_str_dict(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Helper to ensure all dict values are strings for HTTPRequest."""
        return {k: str(v) for k, v in data.items()}

    async def _ocs_request(
        self,
        method: str,
        rel_path: str,
        params: Optional[Dict] = None,
        body: Optional[Dict] = None,
        headers: Optional[Dict] = None
    ) -> HTTPResponse:
        """
        Centralized OCS request handler.
        Ensures 'OCS-APIRequest: true' and 'format=json' are always present.
        """
        _headers = dict(headers or {})
        # Mandatory header for all OCS requests
        _headers.setdefault('OCS-APIRequest', 'true')
        _headers.setdefault('Content-Type', 'application/x-www-form-urlencoded')

        _query = dict(params or {})
        _query.setdefault('format', 'json') # Force JSON response instead of XML

        url = f"{self.base_url}{rel_path}"

        req = HTTPRequest(
            method=method,
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query=self._as_str_dict(_query),
            body=body
        )
        return await self._client.execute(req)



    # WebDAV
    # XML Namespaces used in Nextcloud WebDAV
    XML_NAMESPACES = (
        'xmlns:d="DAV:" '
        'xmlns:oc="http://owncloud.org/ns" '
        'xmlns:nc="http://nextcloud.org/ns" '
        'xmlns:ocs="http://open-collaboration-services.org/ns" '
        'xmlns:ocm="http://open-cloud-mesh.org/ns"'
    )

    # Standard properties to request during PROPFIND
    PROPFIND_PROPERTIES = """
        <d:getlastmodified />
        <d:getetag />
        <d:getcontenttype />
        <d:resourcetype />
        <oc:fileid />
        <oc:permissions />
        <oc:size />
        <d:getcontentlength />
        <nc:has-preview />
        <oc:favorite />
        <oc:comments-unread />
        <oc:owner-display-name />
        <oc:share-types />
        <nc:contained-folder-count />
        <nc:contained-file-count />
        <d:displayname />
        <nc:is-encrypted />
        <nc:mount-type />
        <oc:checksums />
    """

    async def list_directory(
        self,
        user_id: str,
        path: str = "",
        depth: int = 1,
        properties: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        List contents of a folder (PROPFIND).
        Uses standard wrapper as the body is XML text.
        """
        props = properties if properties else self.PROPFIND_PROPERTIES

        body = f"""<?xml version="1.0"?>
        <d:propfind {self.XML_NAMESPACES}>
          <d:prop>
            {props}
          </d:prop>
        </d:propfind>
        """

        _headers = headers or {}
        _headers['Depth'] = str(depth)

        return await self._webdav_request(
            'PROPFIND',
            user_id,
            path,
            body=body,
            headers=_headers
        )

    async def download_file(
        self,
        user_id: str,
        path: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Download a file (GET).
        """
        url = self._build_webdav_url(user_id, path)

        req = HTTPRequest(
            method='GET',
            url=url,
            headers=self._as_str_dict(headers or {}),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    async def upload_file(
        self,
        user_id: str,
        path: str,
        data: bytes,
        total_length: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Upload a file (PUT).
        """
        url = self._build_webdav_url(user_id, path)

        _headers = headers or {}
        if total_length is not None:
            _headers['OC-Total-Length'] = str(total_length)

        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=data
        )
        return await self._client.execute(req)

    async def create_folder(
        self,
        user_id: str,
        path: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Create a new folder (MKCOL)."""
        return await self._webdav_request('MKCOL', user_id, path, headers=headers)

    async def delete_resource(
        self,
        user_id: str,
        path: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Delete a file or folder (DELETE)."""
        return await self._webdav_request('DELETE', user_id, path, headers=headers)

    async def move_resource(
        self,
        user_id: str,
        source_path: str,
        dest_path: str,
        overwrite: bool = True,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Move a file or folder (MOVE)."""
        dest_url = self._build_webdav_url(user_id, dest_path)

        _headers = headers or {}
        _headers['Destination'] = dest_url
        _headers['Overwrite'] = 'T' if overwrite else 'F'

        return await self._webdav_request('MOVE', user_id, source_path, headers=_headers)

    async def copy_resource(
        self,
        user_id: str,
        source_path: str,
        dest_path: str,
        overwrite: bool = True,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Copy a file or folder (COPY)."""
        dest_url = self._build_webdav_url(user_id, dest_path)

        _headers = headers or {}
        _headers['Destination'] = dest_url
        _headers['Overwrite'] = 'T' if overwrite else 'F'

        return await self._webdav_request('COPY', user_id, source_path, headers=_headers)

    async def set_favorite_status(
        self,
        user_id: str,
        path: str,
        is_favorite: bool,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Mark/Unmark a file as favorite (PROPPATCH)."""
        fav_value = "1" if is_favorite else "0"

        body = f"""<?xml version="1.0"?>
        <d:propertyupdate xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
          <d:set>
            <d:prop>
              <oc:favorite>{fav_value}</oc:favorite>
            </d:prop>
          </d:set>
        </d:propertyupdate>
        """

        return await self._webdav_request('PROPPATCH', user_id, path, body=body, headers=headers)

    async def list_favorites(
        self,
        user_id: str,
        path: str = "",
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Retrieve all favorites for a user (REPORT)."""
        body = f"""<?xml version="1.0"?>
        <oc:filter-files {self.XML_NAMESPACES}>
             <oc:filter-rules>
                 <oc:favorite>1</oc:favorite>
             </oc:filter-rules>
             <d:prop>
                {self.PROPFIND_PROPERTIES}
             </d:prop>
        </oc:filter-files>
        """

        return await self._webdav_request('REPORT', user_id, path, body=body, headers=headers)

    # WebDAV Search
    async def execute_webdav_search(
        self,
        search_xml: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Execute a raw WebDAV SEARCH request.
        This is the low-level method used to send custom XML queries defined
        in RFC 5323. It targets the root DAV endpoint.
        Args:
            search_xml: The full XML body defining the search query.
                        Ref: 'Making search requests'.
                        Must include <d:select>, <d:from>, <d:where>, etc.
        """
        # The Search endpoint is the ROOT WebDAV URL, not the user's file directory
        # Ref: "Search requests can be made by sending a SEARCH http request to .../remote.php/dav/"
        url = f"{self.base_url}/remote.php/dav/"

        _headers = headers or {}
        _headers.setdefault('Content-Type', 'text/xml')

        req = HTTPRequest(
            method='SEARCH',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=search_xml
        )
        return await self._client.execute(req)

    # Convenience Methods (Based on Documentation Examples)
    async def get_file_by_internal_id(
        self,
        user_id: str,
        file_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Find a file by its internal Nextcloud File ID (oc:fileid).
        Ref: 'Get a file by id' example.
        This is useful because PROPFIND requires a path, but SEARCH can find a path given an ID.
        """
        # XML structure based on 'Get a file by id' example
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
        <d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:basicsearch>
                <d:select>
                    <d:prop>
                        <d:displayname/>
                        <d:getcontenttype/>
                        <oc:fileid/>
                        <d:getetag/>
                        <oc:size/>
                    </d:prop>
                </d:select>
                <d:from>
                    <d:scope>
                        <d:href>/files/{user_id}</d:href>
                        <d:depth>infinity</d:depth>
                    </d:scope>
                </d:from>
                <d:where>
                    <d:eq>
                        <d:prop>
                            <oc:fileid/>
                        </d:prop>
                        <d:literal>{escape(file_id)}</d:literal>
                    </d:eq>
                </d:where>
            </d:basicsearch>
        </d:searchrequest>"""

        return await self.execute_webdav_search(xml_body, headers=headers)

    async def search_files_by_content_type(
        self,
        user_id: str,
        content_type: str = "text/%",
        path: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Search for files matching a specific mime-type (e.g., 'text/%' or 'image/png').
        Ref: 'Search for all plain text files' example.
        """
        # Scope defaults to user root, or a specific subfolder if provided
        scope_path = f"/files/{user_id}/{path.lstrip('/')}" if path else f"/files/{user_id}"

        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
        <d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
            <d:basicsearch>
                <d:select>
                    <d:prop>
                        <d:displayname/>
                        <d:getcontenttype/>
                        <oc:size/>
                    </d:prop>
                </d:select>
                <d:from>
                    <d:scope>
                        <d:href>{scope_path}</d:href>
                        <d:depth>infinity</d:depth>
                    </d:scope>
                </d:from>
                <d:where>
                    <d:like>
                        <d:prop>
                            <d:getcontenttype/>
                        </d:prop>
                        <d:literal>{escape(content_type)}</d:literal>
                    </d:like>
                </d:where>
                <d:orderby>
                    <d:order>
                        <d:prop>
                            <oc:size/>
                        </d:prop>
                        <d:ascending/>
                    </d:order>
                </d:orderby>
            </d:basicsearch>
        </d:searchrequest>"""

        return await self.execute_webdav_search(xml_body, headers=headers)

    async def search_files_modified_after(
        self,
        user_id: str,
        timestamp_iso: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Get all files last modified after a given date.
        Args:
            timestamp_iso: ISO 8601 format (e.g., '2021-01-01T17:00:00Z').
        Ref: 'Get all files last modified after a given date' example.
        """
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
        <d:searchrequest xmlns:d="DAV:">
            <d:basicsearch>
                <d:select>
                    <d:prop>
                        <d:displayname/>
                        <d:getlastmodified/>
                    </d:prop>
                </d:select>
                <d:from>
                    <d:scope>
                        <d:href>/files/{user_id}</d:href>
                        <d:depth>infinity</d:depth>
                    </d:scope>
                </d:from>
                <d:where>
                    <d:gt>
                        <d:prop>
                            <d:getlastmodified/>
                        </d:prop>
                        <d:literal>{escape(timestamp_iso)}</d:literal>
                    </d:gt>
                </d:where>
            </d:basicsearch>
        </d:searchrequest>"""

        return await self.execute_webdav_search(xml_body, headers=headers)

    async def search_recent_files(
        self,
        user_id: str,
        limit: int = 5,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Search for recent files (exclude directories), ordered by modification date.
        Ref: 'Search for all common files ... and limit the result' example.
        """
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
        <d:searchrequest xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
             <d:basicsearch>
                 <d:select>
                     <d:prop>
                         <oc:fileid/>
                         <d:getcontenttype/>
                         <d:getetag/>
                         <oc:size/>
                         <d:getlastmodified/>
                         <d:resourcetype/>
                     </d:prop>
                 </d:select>
                 <d:from>
                     <d:scope>
                         <d:href>/files/{user_id}</d:href>
                         <d:depth>infinity</d:depth>
                     </d:scope>
                 </d:from>
                 <d:where>
                     <d:not>
                         <d:is-collection/>
                     </d:not>
                 </d:where>
                 <d:orderby>
                    <d:order>
                        <d:prop>
                            <d:getlastmodified/>
                        </d:prop>
                        <d:descending/>
                     </d:order>
                 </d:orderby>
                 <d:limit>
                   <d:nresults>{limit}</d:nresults>
                 </d:limit>
            </d:basicsearch>
        </d:searchrequest>"""

        return await self.execute_webdav_search(xml_body, headers=headers)

    # Trashbin
    async def list_trashbin(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        List the content of the trashbin (PROPFIND).
        Includes special trashbin properties: filename, original location, deletion time.
        Ref: 'Listing the trashbin content'
        """
        # We need the standard properties plus the specific trashbin ones
        trash_props = """
            <d:getlastmodified />
            <d:getcontentlength />
            <d:resourcetype />
            <nc:trashbin-filename />
            <nc:trashbin-original-location />
            <nc:trashbin-deletion-time />
        """

        body = f"""<?xml version="1.0"?>
        <d:propfind {self.XML_NAMESPACES}>
          <d:prop>
            {trash_props}
          </d:prop>
        </d:propfind>
        """

        # Note: The URL is specific to the trashbin
        url = f"{self.base_url}/remote.php/dav/trashbin/{user_id}/trash"

        _headers = headers or {}
        _headers.setdefault('Depth', '1')
        _headers.setdefault('Content-Type', 'application/xml; charset=utf-8')

        req = HTTPRequest(
            method='PROPFIND',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)

    async def restore_trashbin_item(
        self,
        user_id: str,
        item_name: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Restore an item from the trashbin.
        Action: MOVES the item from the 'trash' endpoint to the 'restore' endpoint.
        The server automatically determines the original location.
        Args:
            user_id: The user ID.
            item_name: The name of the item inside the trashbin (e.g., 'file.txt.d12345').
        Ref: 'Restoring from the trashbin'
        """
        # Source: .../trashbin/USER/trash/ITEM
        source_url = f"{self.base_url}/remote.php/dav/trashbin/{user_id}/trash/{item_name}"

        # Destination: .../trashbin/USER/restore/ITEM
        # (Moving it here triggers the auto-restore)
        dest_url = f"{self.base_url}/remote.php/dav/trashbin/{user_id}/restore/{item_name}"

        _headers = headers or {}
        _headers['Destination'] = dest_url

        # Standard WebDAV MOVE
        req = HTTPRequest(
            method='MOVE',
            url=source_url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    async def delete_trashbin_item(
        self,
        user_id: str,
        item_name: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Permanently delete a specific item from the trashbin.
        Ref: 'Deleting from the trashbin'
        """
        url = f"{self.base_url}/remote.php/dav/trashbin/{user_id}/trash/{item_name}"

        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=self._as_str_dict(headers or {}),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    async def empty_trashbin(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Empty the trashbin (Delete all items).
        Ref: 'Emptying the trashbin'
        """
        url = f"{self.base_url}/remote.php/dav/trashbin/{user_id}/trash"

        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=self._as_str_dict(headers or {}),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    # File Versions
    async def list_file_versions(
        self,
        user_id: str,
        file_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        List all available versions of a specific file.
        The 'name' of each version in the response is its timestamp.
        Args:
            user_id: The user ID.
            file_id: The Nextcloud internal file ID (oc:fileid).
        Ref: 'Listing the versions of a file'
        """
        # Endpoint: .../versions/USER/versions/FILEID
        url = f"{self.base_url}/remote.php/dav/versions/{user_id}/versions/{file_id}"

        # Standard PROPFIND body
        body = f"""<?xml version="1.0"?>
        <d:propfind {self.XML_NAMESPACES}>
          <d:prop>
            <d:getlastmodified />
            <d:getcontentlength />
            <d:getcontenttype />
          </d:prop>
        </d:propfind>
        """

        _headers = headers or {}
        _headers.setdefault('Depth', '1')
        _headers.setdefault('Content-Type', 'application/xml; charset=utf-8')

        req = HTTPRequest(
            method='PROPFIND',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)

    async def restore_file_version(
        self,
        user_id: str,
        file_id: str,
        version_timestamp: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Restore a specific version of a file.
        Action: MOVES the version (identified by timestamp) to the restore endpoint.
        Args:
            user_id: The user ID.
            file_id: The internal file ID.
            version_timestamp: The name of the version (returned by list_file_versions),
                               which is typically the timestamp.
        Ref: 'Restoring a version'
        """
        # Source: .../versions/USER/versions/FILEID/TIMESTAMP
        source_url = f"{self.base_url}/remote.php/dav/versions/{user_id}/versions/{file_id}/{version_timestamp}"

        # Destination: .../versions/USER/restore/TIMESTAMP
        # Moving the version here triggers the restore logic on the server.
        dest_url = f"{self.base_url}/remote.php/dav/versions/{user_id}/restore/{version_timestamp}"

        _headers = headers or {}
        _headers['Destination'] = dest_url

        req = HTTPRequest(
            method='MOVE',
            url=source_url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    # Chunked Uploads
    async def initiate_chunked_upload(
        self,
        user_id: str,
        upload_id: str,
        dest_path: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Start a chunked upload by creating the upload directory.
        Args:
            user_id: The user ID.
            upload_id: A unique ID for this upload (e.g., a random UUID).
            dest_path: The FINAL path where the file will be stored (e.g. 'dest/file.zip').
        Ref: 'Starting a chunked upload'
        """
        # Uploads Endpoint: .../dav/uploads/USER/UPLOAD_ID
        url = f"{self.base_url}/remote.php/dav/uploads/{user_id}/{upload_id}"

        # Calculate the full destination URL (Required header for v2)
        final_dest_url = self._build_webdav_url(user_id, dest_path)

        _headers = headers or {}
        _headers['Destination'] = final_dest_url

        req = HTTPRequest(
            method='MKCOL',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    async def upload_chunk(
        self,
        user_id: str,
        upload_id: str,
        chunk_index: int,
        data: bytes,
        dest_path: str,
        total_length: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Upload a single chunk.
        """
        # Pad chunk index to 5 digits (e.g., 1 -> "00001") to ensure correct sort order
        chunk_name = f"{chunk_index:05d}"

        url = f"{self.base_url}/remote.php/dav/uploads/{user_id}/{upload_id}/{chunk_name}"
        final_dest_url = self._build_webdav_url(user_id, dest_path)

        _headers = headers or {}
        # Required Headers for v2
        _headers['Destination'] = final_dest_url
        _headers['OC-Total-Length'] = str(total_length)

        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=data
        )
        return await self._client.execute(req)

    async def complete_chunked_upload(
        self,
        user_id: str,
        upload_id: str,
        dest_path: str,
        total_length: int,
        mtime: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Assemble the chunks by moving the special '.file' to the destination.
        Ref: 'Assembling the chunks'
        """
        # Source is the special .file inside the upload folder
        source_url = f"{self.base_url}/remote.php/dav/uploads/{user_id}/{upload_id}/.file"

        final_dest_url = self._build_webdav_url(user_id, dest_path)

        _headers = headers or {}
        _headers['Destination'] = final_dest_url
        _headers['OC-Total-Length'] = str(total_length)

        if mtime is not None:
            _headers['X-OC-Mtime'] = str(mtime)

        req = HTTPRequest(
            method='MOVE',
            url=source_url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    async def abort_chunked_upload(
        self,
        user_id: str,
        upload_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Abort the upload by deleting the upload folder.
        Ref: 'Aborting the upload'
        """
        url = f"{self.base_url}/remote.php/dav/uploads/{user_id}/{upload_id}"

        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=self._as_str_dict(headers or {}),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    # File Bulk Upload
    async def bulk_upload_files(
        self,
        user_id: str,
        files: List[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Upload multiple small files in a single request (Bulk Upload).
        """

        # Endpoint: .../remote.php/dav/bulk
        url = f"{self.base_url}/remote.php/dav/bulk"

        # Generate a unique boundary
        boundary = f"boundary_{uuid.uuid4().hex}"
        boundary_bytes = boundary.encode('utf-8')

        # Construct the multipart/related body
        body_parts = []

        for file_info in files:
            path = file_info['path']
            content = file_info['content']
            mtime = file_info.get('mtime', int(datetime.now().timestamp()))

            # Calculate MD5 checksum
            md5_hash = hashlib.md5(content).hexdigest()
            content_length = len(content)

            # 1. Boundary
            body_parts.append(b"--" + boundary_bytes)

            # 2. Headers for this specific part
            headers_part = (
                f"Content-Length: {content_length}\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"X-File-MD5: {md5_hash}\r\n"
                f"X-File-Mtime: {mtime}\r\n"
                f"X-File-Path: {path}\r\n"
                f"\r\n" # Empty line before content
            ).encode('utf-8')

            body_parts.append(headers_part)

            # 3. File Content
            body_parts.append(content)
            body_parts.append(b"\r\n")

        # End Boundary
        body_parts.append(b"--" + boundary_bytes + b"--\r\n")

        # Join all parts to form the full binary payload
        full_body = b"\r\n".join(body_parts)

        # Prepare Request Headers
        _headers = headers or {}
        _headers['Content-Type'] = f"multipart/related; boundary={boundary}"

        req = HTTPRequest(
            method='POST',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=full_body
        )
        return await self._client.execute(req)

    # XML Namespaces specifically for Comments
    COMMENTS_NAMESPACES = (
        'xmlns:d="DAV:" '
        'xmlns:oc="http://owncloud.org/ns"'
    )

    async def create_comment(
        self,
        object_id: str,
        message: str,
        object_type: str = "files",
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Create a new comment on an object.
        Args:
            object_id: The ID of the file or object (e.g., '2156').
            message: The comment text.
            object_type: Usually 'files', but can be other types like 'announcements'.
        Ref: 'POST for creating a comment'
        """
        # Endpoint: .../comments/OBJECTTYPE/OBJECTID
        url = f"{self.base_url}/remote.php/comments/{object_type}/{object_id}"

        # Nextcloud Comments POST typically accepts a JSON body with the message
        body = {
            "message": message,
            "verb": "comment",
            "actorType": "users"
        }

        _headers = headers or {}
        _headers.setdefault('Content-Type', 'application/json')

        req = HTTPRequest(
            method='POST',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)

    async def list_comments(
        self,
        object_id: str,
        object_type: str = "files",
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        List all comments for a specific object.
        Ref: 'PROPFIND to list comments'
        """
        url = f"{self.base_url}/remote.php/comments/{object_type}/{object_id}"

        # We request standard comment properties
        body = f"""<?xml version="1.0"?>
        <d:propfind {self.COMMENTS_NAMESPACES}>
            <d:prop>
                <oc:message />
                <oc:actorId />
                <oc:creationDateTime />
                <oc:verb />
            </d:prop>
        </d:propfind>
        """

        _headers = headers or {}
        _headers.setdefault('Content-Type', 'application/xml')
        _headers.setdefault('Depth', '1')

        req = HTTPRequest(
            method='PROPFIND',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)

    async def search_comments(
        self,
        object_id: str,
        limit: int = 20,
        offset: int = 0,
        datetime_limit: Optional[str] = None,
        object_type: str = "files",
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Search for comments using a REPORT request.
        Args:
            datetime_limit: Date string (e.g. '2016-01-18 22:10:30').
        Ref: 'REPORT request' and 'report.xml example'
        """
        url = f"{self.base_url}/remote.php/comments/{object_type}/{object_id}"

        # Construct the Report XML based on the documentation example
        datetime_xml = f"<oc:datetime>{escape(datetime_limit)}</oc:datetime>" if datetime_limit else ""

        body = f"""<?xml version="1.0" encoding="utf-8" ?>
        <d:report {self.COMMENTS_NAMESPACES}>
            <oc:limit>{limit}</oc:limit>
            <oc:offset>{offset}</oc:offset>
            {datetime_xml}
        </d:report>
        """

        _headers = headers or {}
        _headers.setdefault('Content-Type', 'text/xml')

        req = HTTPRequest(
            method='REPORT',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)

    async def update_comment(
        self,
        object_id: str,
        comment_id: str,
        new_message: str,
        object_type: str = "files",
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Update an existing comment.
        Ref: 'PROPPATCH to update the comment' (on CommentID endpoint)
        """
        # Endpoint: .../comments/OBJECTTYPE/OBJECTID/COMMENTID
        url = f"{self.base_url}/remote.php/comments/{object_type}/{object_id}/{comment_id}"

        body = f"""<?xml version="1.0"?>
        <d:propertyupdate {self.COMMENTS_NAMESPACES}>
            <d:set>
                <d:prop>
                    <oc:message>{escape(new_message)}</oc:message>
                </d:prop>
            </d:set>
        </d:propertyupdate>
        """

        _headers = headers or {}
        _headers.setdefault('Content-Type', 'application/xml')

        req = HTTPRequest(
            method='PROPPATCH',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)

    async def delete_comment(
        self,
        object_id: str,
        comment_id: str,
        object_type: str = "files",
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Delete a comment.
        Ref: 'DELETE to delete it'
        """
        url = f"{self.base_url}/remote.php/comments/{object_type}/{object_id}/{comment_id}"

        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=self._as_str_dict(headers or {}),
            path={},
            query={},
            body=None
        )
        return await self._client.execute(req)

    async def set_read_marker(
        self,
        object_id: str,
        read_marker_time: str,
        object_type: str = "files",
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Update the read marker for the current user on a file.
        Args:
            read_marker_time: ISO8601 timestamp (e.g., '2016-01-20T14:01:24+00:00').
        Ref: 'PROPPATCH to update the read mark'
        """
        url = f"{self.base_url}/remote.php/comments/{object_type}/{object_id}"

        body = f"""<?xml version="1.0"?>
        <d:propertyupdate {self.COMMENTS_NAMESPACES}>
            <d:set>
                <d:prop>
                    <oc:readMarker>{escape(read_marker_time)}</oc:readMarker>
                </d:prop>
            </d:set>
        </d:propertyupdate>
        """

        _headers = headers or {}
        _headers.setdefault('Content-Type', 'application/xml')

        req = HTTPRequest(
            method='PROPPATCH',
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)

    # Internal WebDAV helpers
    def _build_webdav_url(self, user_id: str, path: str) -> str:
        """Constructs the full WebDAV URL."""
        clean_path = path.lstrip('/')
        # Assumes base_url has no trailing slash
        return f"{self.base_url}/remote.php/dav/files/{user_id}/{clean_path}"

    async def _webdav_request(
        self,
        method: str,
        user_id: str,
        path: str,
        body: Union[str, bytes, None] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """
        Specific request handler for WebDAV.
        """
        url = self._build_webdav_url(user_id, path)
        _headers = dict(headers or {})

        # If body is string (XML), ensure content type
        if isinstance(body, str):
            _headers.setdefault('Content-Type', 'application/xml; charset=utf-8')

        req = HTTPRequest(
            method=method,
            url=url,
            headers=self._as_str_dict(_headers),
            path={},
            query={},
            body=body
        )
        return await self._client.execute(req)
